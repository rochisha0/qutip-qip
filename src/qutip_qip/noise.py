"""Noise of quantum hardware."""

import numbers
import warnings
from collections.abc import Iterable
from copy import deepcopy
import numpy as np

from qutip import Qobj, QobjEvo, basis, sigmaz, destroy, num, qeye, tensor
from .operations import expand_operator
from .pulse import Pulse


__all__ = [
    "Noise",
    "DecoherenceNoise",
    "RelaxationNoise",
    "ControlAmpNoise",
    "RandomNoise",
    "process_noise",
    "ZZCrossTalk",
]


def process_noise(
    pulses,
    noise_list,
    dims,
    t1=None,
    t2=None,
    device_noise=False,
    spline_kind=None,
):
    """
    Apply noise to the input list of pulses. It does not modify the input
    pulse, but return a new one containing the noise.

    Parameters
    ----------
    pulses: list of :class:`.Pulse`
        The input pulses, on which the noise object will be applied.
    noise_list: list of :class:`.Noise`
        A list of noise objects.
    dims: int or list
        Dimension of the system.
        If int, we assume it is the number of qubits in the system.
        If list, it is the dimension of the component systems.
    t1: float or list, optional
        Characterize the decoherence of amplitude damping for
        each qubit. A list of size `N` or a float for all qubits.
    t2: float or list, optional
        Characterize the decoherence of dephasing for
        each qubit. A list of size `N` or a float for all qubits.
    device_noise: bool
        If pulse independent noise such as relaxation are included.
        Default is False.

    Returns
    -------
    noisy_pulses: list of :class:`.Pulse`
        The noisy pulses, including the system noise.
    """
    noise_list = noise_list.copy()
    noisy_pulses = deepcopy(pulses)
    systematic_noise = Pulse(
        None, None, label="systematic_noise", spline_kind=spline_kind
    )

    if (t1 is not None) or (t2 is not None):
        noise_list.append(RelaxationNoise(t1, t2))

    for noise in noise_list:
        if (
            isinstance(noise, (DecoherenceNoise, RelaxationNoise))
            and not device_noise
        ):
            pass
        else:
            noisy_pulses, systematic_noise = noise._apply_noise(
                dims=dims,
                pulses=noisy_pulses,
                systematic_noise=systematic_noise,
            )

    if device_noise:
        return noisy_pulses + [systematic_noise]
    else:
        return noisy_pulses


class Noise(object):
    """
    The base class representing noise in a processor.
    The noise object can be added to :class:`.device.Processor` and
    contributes to evolution.
    """

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        """
        Return the input pulses list with noise added and
        the pulse independent noise in a dummy :class:`.Pulse` object.
        This is a template method, a method with the same name and signatures
        needs to be defined in the subclasses.

        Parameters
        ----------
        dims: list, optional
            The dimension of the components system, the default value is
            [2,2...,2] for qubits system.

        pulses : list of :class:`.Pulse`
            The input pulses. The noise will be added to pulses in this list.

        systematic_noise : :class:`.Pulse`
            The dummy pulse with no ideal control element.

        Returns
        -------
        noisy_pulses: list of :class:`.Pulse`
            Noisy pulses.

        systematic_noise : :class:`.Pulse`
            The dummy pulse representing pulse-independent noise.
        """
        get_noisy_dynamics = getattr(self, "get_noisy_dynamics", None)
        if get_noisy_dynamics is not None:
            warnings.warn(
                "Using get_noisy_dynamics as the hook function for custom "
                "noise will be deprecated, "
                "please use get_noisy_pulses instead.",
                PendingDeprecationWarning,
            )
            return self.get_noisy_dynamics(dims, pulses, systematic_noise)
        raise NotImplementedError(
            "Subclass error needs a method"
            "`get_noisy_pulses` to process the noise."
        )

    def _apply_noise(self, pulses=None, systematic_noise=None, dims=None):
        """
        For backward compatibility, in case the method has no return value
        or only return the pulse.
        """
        result = self.get_noisy_pulses(
            pulses=pulses, systematic_noise=systematic_noise, dims=dims
        )
        if result is None:  # in-place change
            pass
        elif isinstance(result, tuple) and len(result) == 2:
            pulses, systematic_noise = result
        # only pulse
        elif isinstance(result, list) and len(result) == len(pulses):
            pulses = result
        else:
            raise TypeError(
                "Returned value of get_noisy_pulses not understood."
            )
        return pulses, systematic_noise


class DecoherenceNoise(Noise):
    """
    The decoherence noise in a processor. It generates lindblad noise
    according to the given collapse operator `c_ops`.

    Parameters
    ----------
    c_ops : :class:`qutip.Qobj` or list
        The Hamiltonian representing the dynamics of the noise.
    targets: int or list, optional
        The indices of qubits that are acted on. Default is on all
        qubits
    coeff: list, optional
        A list of the coefficients for the control Hamiltonians.
    tlist: array_like, optional
        A NumPy array specifies the time of each coefficient.
    all_qubits: bool, optional
        If `c_ops` contains only single qubits collapse operator,
        ``all_qubits=True`` will allow it to be applied to all qubits.

    Attributes
    ----------
    c_ops : :class:`qutip.Qobj` or list
        The Hamiltonian representing the dynamics of the noise.
    targets: int or list
        The indices of qubits that are acted on.
    coeff: list
        A list of the coefficients for the control Hamiltonians.
    tlist: array_like
        A NumPy array specifies the time of each coefficient.
    all_qubits: bool
        If `c_ops` contains only single qubits collapse operator,
        ``all_qubits=True`` will allow it to be applied to all qubits.
    """

    def __init__(
        self, c_ops, targets=None, coeff=None, tlist=None, all_qubits=False
    ):
        if isinstance(c_ops, Qobj):
            self.c_ops = [c_ops]
        else:
            self.c_ops = c_ops
        self.coeff = coeff
        self.tlist = tlist
        self.targets = targets
        if all_qubits:
            if not all([c_op.dims == [[2], [2]] for c_op in self.c_ops]):
                raise ValueError(
                    "The operator is not a single qubit operator, "
                    "thus cannot be applied to all qubits"
                )
        self.all_qubits = all_qubits

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        """
        Return the input pulses list with noise added and
        the pulse independent noise in a dummy :class:`.Pulse` object.

        Parameters
        ----------
        dims: list, optional
            The dimension of the components system, the default value is
            [2, 2, ..., 2] for a system of qubits.
        pulses : list of :class:`.Pulse`
            The input pulses. The noise will be added to pulses in this list.
        systematic_noise : :class:`.Pulse`
            The dummy pulse with no ideal control element.

        Returns
        -------
        noisy_pulses: list of :class:`.Pulse`
            Noisy pulses.
        systematic_noise : :class:`.Pulse`
            The dummy pulse representing pulse-independent noise.
        """
        if systematic_noise is None:
            systematic_noise = Pulse(None, None, label="system")
        N = len(dims)
        # time-independent
        if (self.coeff is None) and (self.tlist is None):
            self.coeff = True

        for c_op in self.c_ops:
            if self.all_qubits:
                for targets in range(N):
                    systematic_noise.add_lindblad_noise(
                        c_op, targets, self.tlist, self.coeff
                    )
            else:
                systematic_noise.add_lindblad_noise(
                    c_op, self.targets, self.tlist, self.coeff
                )
        return pulses, systematic_noise


class RelaxationNoise(Noise):
    """
    The decoherence on each qubit characterized by two time scales t1 and t2.

    Parameters
    ----------
    t1: float or list, optional
        Characterize the decoherence of amplitude damping for
        each qubit.
    t2: float or list, optional
        Characterize the decoherence of dephasing for
        each qubit.
    targets: int or list, optional
        The indices of qubits that are acted on. Default is on all
        qubits

    Attributes
    ----------
    t1: float or list
        Characterize the decoherence of amplitude damping for
        each qubit.
    t2: float or list
        Characterize the decoherence of dephasing for
        each qubit.
    targets: int or list
        The indices of qubits that are acted on.
    """

    def __init__(self, t1=None, t2=None, targets=None):
        self.t1 = t1
        self.t2 = t2
        self.targets = targets

    def _T_to_list(self, T, N):
        """
        Check if the relaxation time is valid

        Parameters
        ----------
        T: float or list
            The relaxation time
        N: int
            The number of qubits.

        Returns
        -------
        T: list
            The relaxation time in Python list form
        """
        if (isinstance(T, numbers.Real) and T > 0) or T is None:
            return [T] * N
        elif isinstance(T, Iterable) and len(T) == N:
            return T
        else:
            raise ValueError(
                "Invalid relaxation time T={},"
                "either the length is not equal to the number of qubits, "
                "or T is not a positive number.".format(T)
            )

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        """
        Return the input pulses list with noise added and
        the pulse independent noise in a dummy :class:`.Pulse` object.

        Parameters
        ----------
        dims: list, optional
            The dimension of the components system, the default value is
            [2,2...,2] for qubits system.
        pulses : list of :class:`.Pulse`
            The input pulses. The noise will be added to pulses in this list.
        systematic_noise : :class:`.Pulse`
            The dummy pulse with no ideal control element.

        Returns
        -------
        noisy_pulses: list of :class:`.Pulse`
            Noisy pulses.
        systematic_noise : :class:`.Pulse`
            The dummy pulse representing pulse-independent noise.
        """
        if systematic_noise is None:
            systematic_noise = Pulse(None, None, label="system")
        N = len(dims)

        self.t1 = self._T_to_list(self.t1, N)
        self.t2 = self._T_to_list(self.t2, N)
        if len(self.t1) != N or len(self.t2) != N:
            raise ValueError(
                "Length of t1 or t2 does not match N, "
                "len(t1)={}, len(t2)={}".format(len(self.t1), len(self.t2))
            )

        if self.targets is None:
            targets = range(N)
        else:
            targets = self.targets
        for qu_ind in targets:
            t1 = self.t1[qu_ind]
            t2 = self.t2[qu_ind]
            if t1 is not None:
                op = 1 / np.sqrt(t1) * destroy(dims[qu_ind])
                systematic_noise.add_lindblad_noise(op, qu_ind, coeff=True)
            if t2 is not None:
                # Keep the total dephasing ~ exp(-t/t2)
                if t1 is not None:
                    if 2 * t1 < t2:
                        raise ValueError(
                            "t1={}, t2={} does not fulfill "
                            "2*t1>t2".format(t1, t2)
                        )
                    T2_eff = 1.0 / (1.0 / t2 - 1.0 / 2.0 / t1)
                else:
                    T2_eff = t2
                op = 1 / np.sqrt(2 * T2_eff) * 2 * num(dims[qu_ind])
                systematic_noise.add_lindblad_noise(op, qu_ind, coeff=True)
        return pulses, systematic_noise


class ControlAmpNoise(Noise):
    """
    The noise in the amplitude of the control pulse.

    Parameters
    ----------
    coeff: list
        A list of the coefficients for the control Hamiltonians.
        For available choices, see :class:`qutip.QobjEvo`.
    tlist: array_like, optional
        A NumPy array specifies the time of each coefficient.
    indices: list of int, optional
        The indices of target pulse in the list of pulses.

    Attributes
    ----------
    coeff: list
        A list of the coefficients for the control Hamiltonians.
        For available choices, see :class:`qutip.QobjEvo`.
    tlist: array_like
        A NumPy array specifies the time of each coefficient.
    indices: list of int
        The indices of target pulse in the list of pulses.

    """

    def __init__(self, coeff, tlist=None, indices=None):
        self.coeff = coeff
        self.tlist = tlist
        self.indices = indices

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        if pulses is None:
            pulses = []
        if self.indices is None:
            indices = range(len(pulses))
        else:
            indices = self.indices
        for i in indices:
            pulse = pulses[i]
            if isinstance(self.coeff, (int, float)):
                coeff = pulse.coeff * self.coeff
            else:
                coeff = self.coeff
            if self.tlist is None:
                tlist = pulse.tlist
            else:
                tlist = self.tlist
            pulses[i].add_coherent_noise(
                pulse.qobj, pulse.targets, tlist, coeff
            )
        return pulses, systematic_noise


class RandomNoise(ControlAmpNoise):
    """
    Random noise in the amplitude of the control pulse. The arguments for
    the random generator need to be given as key word arguments.

    Parameters
    ----------
    dt: float, optional
        The time interval between two random amplitude. The coefficients
        of the noise are the same within this time range.
    rand_gen: numpy.random, optional
        A random generator in numpy.random, it has to take a ``size``
        parameter as the size of random numbers in the output array.
    indices: list of int, optional
        The indices of target pulse in the list of pulses.
    **kwargs:
        Key word arguments for the random number generator.

    Attributes
    ----------
    dt: float, optional
        The time interval between two random amplitude. The coefficients
        of the noise are the same within this time range.
    rand_gen: numpy.random, optional
        A random generator in numpy.random, it has to take a ``size``
        parameter.
    indices: list of int
        The indices of target pulse in the list of pulses.
    **kwargs:
        Key word arguments for the random number generator.

    Examples
    --------
    >>> gaussnoise = RandomNoise( \
            dt=0.1, rand_gen=np.random.normal, loc=mean, scale=std) \
            # doctest: +SKIP
    """

    def __init__(self, dt, rand_gen, indices=None, **kwargs):
        super(RandomNoise, self).__init__(coeff=None, tlist=None)
        self.rand_gen = rand_gen
        self.kwargs = kwargs
        if "size" in kwargs:
            raise ValueError("size is predetermined inside the noise object.")
        self.dt = dt
        self.indices = indices

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        """
        Return the input pulses list with noise added and
        the pulse independent noise in a dummy :class:`.Pulse` object.

        Parameters
        ----------
        dims: list, optional
            The dimension of the components system, the default value is
            [2,2...,2] for qubits system.
        pulses : list of :class:`.Pulse`
            The input pulses. The noise will be added to pulses in this list.
        systematic_noise : :class:`.Pulse`
            The dummy pulse with no ideal control element.

        Returns
        -------
        noisy_pulses: list of :class:`.Pulse`
            Noisy pulses.
        systematic_noise : :class:`.Pulse`
            The dummy pulse representing pulse-independent noise.
        """
        if pulses is None:
            pulses = []
        if self.indices is None:
            indices = range(len(pulses))
        else:
            indices = self.indices
        t_max = -np.inf
        t_min = np.inf
        for pulse in pulses:
            t_max = max(max(pulse.tlist), t_max)
            t_min = min(min(pulse.tlist), t_min)
        # create new tlist and random coeff
        num_rand = int(np.floor((t_max - t_min) / self.dt)) + 1
        tlist = np.arange(0, self.dt * num_rand, self.dt)[:num_rand] + t_min
        # [:num_rand] for round off error like 0.2*6=1.2000000000002

        for i in indices:
            pulse = pulses[i]
            coeff = self.rand_gen(**self.kwargs, size=num_rand)
            pulses[i].add_coherent_noise(
                pulse.qobj, pulse.targets, tlist, coeff
            )
        return pulses, systematic_noise


class ZZCrossTalk(Noise):
    """
    An always-on ZZ cross talk noise with the corresponding coefficient
    on each pair of qubits.
    The operator acts only one the lowerest two levels and
    is 0 on higher level.
    Equivalent to ``tensor(sigmaz(), sigmaz())``.

    Parameters
    ----------
    params:
        Parameters computed from a :class:`.SCQubits`.
    """

    def __init__(self, params):
        self.params = params

    def get_noisy_pulses(self, dims=None, pulses=None, systematic_noise=None):
        """
        Return the input pulses list with noise added and
        the pulse independent noise in a dummy :class:`.Pulse` object.

        Parameters
        ----------
        dims: list, optional
            The dimension of the components system, the default value is
            [2,2...,2] for qubits system.
        pulses : list of :class:`.Pulse`
            The input pulses. The noise will be added to pulses in this list.
        systematic_noise : :class:`.Pulse`
            The dummy pulse with no ideal control element.

        Returns
        -------
        noisy_pulses: list of :class:`.Pulse`
            Noisy pulses.
        systematic_noise : :class:`.Pulse`
            The dummy pulse representing pulse-independent noise.
        """
        J = self.params["J"]
        wr_dr = self.params["wr_dressed"]
        wr = self.params["wr"]
        wq_dr_cav = self.params["wq_dressed_cavity"]
        wq_dr = self.params["wq_dressed"]
        wq = self.params["wq"]
        alpha = self.params["alpha"]
        omega = self.params["omega_cr"]
        for i in range(len(dims) - 1):
            d1 = dims[i]
            d2 = dims[i + 1]
            destroy_op1 = destroy(d1)
            destroy_op2 = destroy(d2)
            projector1 = (
                basis(d1, 0) * basis(d1, 0).dag()
                + basis(d1, 1) * basis(d2, 1).dag()
            )
            projector2 = (
                basis(d2, 0) * basis(d2, 0).dag()
                + basis(d2, 1) * basis(d2, 1).dag()
            )
            z1 = (
                projector1
                * (destroy_op1.dag() * destroy_op1 * 2 - qeye(d1))
                * projector1
            )
            z2 = (
                projector2
                * (destroy_op2.dag() * destroy_op2 * 2 - qeye(d1))
                * projector2
            )
            zz_op = tensor(z1, z2)
            zz_coeff = (
                1 / (wq_dr_cav[i] - wq_dr_cav[i + 1] - alpha[i + 1])
                - 1 / (wq_dr_cav[i] - wq_dr_cav[i + 1] + alpha[i])
            ) * J[i] ** 2
            systematic_noise.add_control_noise(
                zz_coeff * zz_op / 2,
                targets=[i, i + 1],
                coeff=True,
                tlist=None,
            )
        return pulses, systematic_noise
