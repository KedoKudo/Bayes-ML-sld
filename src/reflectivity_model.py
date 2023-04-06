"""
TODO: we probably don't need to save the amplitude since we can compute it. Depends of time.
"""
import sys
import os
import numpy as np
np.random.seed(42)



import json
import refl1d
from refl1d.names import *


def calculate_reflectivity_from_profile(q, z_step, sld, q_resolution=0.025):
    """
        Reflectivity calculation using refl1d from an array of microslabs
    """
    zeros = np.zeros(len(q))
    dq = q_resolution * q

    # The QProbe object represents the beam
    probe = QProbe(q, dq, data=(zeros, zeros))

    sample = Slab(material=SLD(name='back', rho=sld[0], irho=0), interface=0)

    # Add each layer
    _prev_z = z_step[0]
    for i, _sld in enumerate(sld):
        if i>0:
            thickness = z_step[i] - _prev_z
            sample = sample | Slab(material=SLD(name='l_%d' % i, rho=_sld, irho=0),
                                                thickness=thickness,
                                                interface=0)
        _prev_z = z_step[i]

    probe.background = Parameter(value=0, name='background')
    expt = Experiment(probe=probe, sample=sample)

    _, r = expt.reflectivity()
    return r


def calculate_reflectivity(q, model_description, q_resolution=0.02,
                           z_left=-100, z_right=900, dz=5):
    """
        Reflectivity calculation using refl1d
    """
    zeros = np.zeros(len(q))
    dq = q_resolution * q / 2.355

    # The QProbe object represents the beam
    probe = QProbe(q, dq, data=(zeros, zeros))
    #probe.oversample(11, seed=1)

    layers = model_description['layers']
    sample = Slab(material=SLD(name=layers[0]['name'],
                               rho=layers[0]['sld']), interface=layers[0]['roughness'])
    # Add each layer
    for l in layers[1:]:
        sample = sample | Slab(material=SLD(name=l['name'],
                               rho=l['sld'], irho=l['isld']),
                               thickness=l['thickness'], interface=l['roughness'])

    probe.background = Parameter(value=model_description['background'], name='background')
    expt = Experiment(probe=probe, sample=sample)

    q, r = expt.reflectivity()
    q, a = expt._reflamp()
    slabs = expt._render_slabs()
    slabs._z_left = z_left
    slabs._z_right = z_right
    z, sld, _ = slabs.smooth_profile(dz=dz)

    return r, z, sld


class ReflectivityModels(object):
    # Neutrons come in from the last item in the list
    model_description = dict(layers=[
                                dict(sld=2.07, isld=0, thickness=0, roughness=11.1, name='substrate'),
                                dict(sld=7.53, isld=0, thickness=162.4, roughness=21.9, name='bulk'),
                                dict(sld=4.79, isld=0, thickness=200.3, roughness=24.9, name='oxide'),
                                dict(sld=0, isld=0, thickness=0, roughness=0, name='air')
                         ],
                         scale=1,
                         background=0,
                        )
    parameters = [
                  dict(i=0, par='roughness', bounds=[0, 40]),
                  dict(i=1, par='sld', bounds=[0, 10]),
                  dict(i=1, par='thickness', bounds=[20, 300]),
                  dict(i=1, par='roughness', bounds=[0, 40]),
                  dict(i=2, par='sld', bounds=[1, 10]),
                  dict(i=2, par='thickness', bounds=[50, 300]),
                  dict(i=2, par='roughness', bounds=[0, 40]),
                 ]

    def __init__(self, q=None, name='thin_film', z_left=-100, z_right=900, dz=5):
        self._refl_array = []
        self._z_array = []
        self._sld_array = []
        self._train_pars = None
        self._train_data = None
        self._config_name = name
        self.z_left = z_left
        self.z_right = z_right
        self.dz = dz

        if q is None:
            self.q = np.logspace(np.log10(0.009), np.log10(0.16), num=150)
        else:
            self.q = q

    @classmethod
    def from_dict(cls, pars):
        """
            Create ReflectivityModels object from a dict that
            defines the reflectivity model parameters and how
            the training set should be generated.
        """
        m = cls(None, name=pars['name'])
        m.model_description =  pars['model']
        m.parameters = pars['parameters']
        m.z_left = pars['z_left']
        m.z_right = pars['z_right']
        m.dz = pars['dz']
        return m

    def generate(self, n=100):
        """
            Generate a random sample of models
        """
        # Generate random model parameters
        npars = len(self.parameters)
        random_pars = np.random.uniform(low=-1, high=1, size=[n, npars])
        pars_array = self.to_model_parameters(random_pars)

        # Compute model parameters and reflectivity using these values
        self.compute_reflectivity(pars_array)

    def to_model_parameters(self, pars):
        """
            Transform an array of parameters to a list of calculable models
        """
        pars_array = np.zeros(pars.shape)

        for i, par in enumerate(self.parameters):
            a = (par['bounds'][1]-par['bounds'][0])/2.
            b = (par['bounds'][1]+par['bounds'][0])/2.
            pars_array.T[i] = pars.T[i] * a + b

        return pars_array

    def compute_reflectivity(self, pars_array):
        """
            Transform an array of parameters to a list of calculable models
            and compute reflectivity
        """
        print("Computing reflectivity")

        # Compute reflectivity
        for p in pars_array:
            _desc = self.get_model_description(p)
            r, z, sld = calculate_reflectivity(self.q, _desc,
                                               z_left=self.z_left,
                                               z_right=self.z_right,
                                               dz=self.dz)
            self._refl_array.append(r)
            self._z_array.append(z)
            self._sld_array.append(sld)

    def get_model_description(self, pars):
        """
            Return a model description that we can use to compute reflectivity
        """
        for i, par in enumerate(self.parameters):
            self.model_description['layers'][par['i']][par['par']] = pars[i]
        return self.model_description

    def get_preprocessed_data(self, errors=None):
        """
            Pre-process data
        """
        if errors is None:
            self._train_data = np.log10(self._refl_array*self.q**2/self.q[0]**2)
            #self._train_data = self._refl_array*self.q**2/self.q[0]**2

        self._train_pars = self._sld_array

        return self._train_pars, self._train_data

    def save(self, output_dir=''):
        """
            Save all data relevant to a training set
            @param output_dir: directory used to store training sets
        """
        # Save q values
        np.save(os.path.join(output_dir, "%s_q_values" % self._config_name), self.q)

        # Save training set
        if self._train_data is not None:
            np.save(os.path.join(output_dir, "%s_data" % self._config_name), self._train_data)
            np.save(os.path.join(output_dir, "%s_pars" % self._config_name), self._train_pars)

    def load(self, data_dir=''):
        self.q = np.load(os.path.join(data_dir, "%s_q_values.npy" % self._config_name))
        self._train_data = np.load(os.path.join(data_dir, "%s_data.npy" % self._config_name))
        self._train_pars = np.load(os.path.join(data_dir, "%s_pars.npy" % self._config_name))
        return self.q, self._train_data, self._train_pars