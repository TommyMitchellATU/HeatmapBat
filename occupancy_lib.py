"""Shared M1 occupancy likelihood and simulator for the occupancy notebooks.

Imported by occupancy_modeling.ipynb (real data) and occupancy_simulation.ipynb (recovery
study), so both fit exactly the same model.
"""

import numpy as np
import pymc as pm
import pytensor.tensor as pt

M1_BETA_PRIOR = (2.0, 2.0)
"""Beta(alpha, beta) prior on both psi and p in M1."""


def m1_occupancy_logp(
    psi: pt.TensorLike,
    p: pt.TensorLike,
    site_detected: np.ndarray,
    site_n_surveys: np.ndarray,
) -> pt.TensorVariable:
    """Occupancy part of the M1 log-likelihood, summed over sites (MacKenzie et al. 2002 [6]).

    A site with a detection must be occupied and adds log(psi). A site with none is
    either occupied and missed every survey, or empty:
    log(psi * (1 - p)^K + (1 - psi)). The detection terms of detected sites are left
    to the caller, so the main notebook can keep them as a per-survey Bernoulli for WAIC.
    """
    log_psi = pt.log(psi)
    log_missed_or_empty = pm.math.logaddexp(
        log_psi + site_n_surveys * pt.log1p(-p), pt.log1p(-psi)
    )
    return pt.sum(pt.switch(site_detected, log_psi, log_missed_or_empty))


def build_m1_site_model(
    site_n_detections: np.ndarray, site_n_surveys: np.ndarray
) -> pm.Model:
    """M1 fitted to per-site detection totals instead of per-survey 0/1 data.

    The Binomial over a site's surveys equals the product of its Bernoulli terms up to a
    constant (the binomial coefficient), so the posterior is the same as the main
    notebook's M1. Used by the simulation, where per-site totals are all it generates.
    """
    site_detected = site_n_detections > 0
    with pm.Model() as model:
        psi = pm.Beta("psi", *M1_BETA_PRIOR)
        p = pm.Beta("p", *M1_BETA_PRIOR)
        pm.Potential(
            "occupancy", m1_occupancy_logp(psi, p, site_detected, site_n_surveys)
        )
        detection_logp = pm.logp(
            pm.Binomial.dist(n=site_n_surveys, p=p), site_n_detections
        )
        # Undetected sites' surveys are already inside the occupancy term.
        pm.Potential("detection", pt.sum(pt.switch(site_detected, detection_logp, 0.0)))
    return model


def simulate_m1(
    n_sites: int, n_surveys: int, psi: float, p: float, rng: np.random.Generator
) -> np.ndarray:
    """Per-site detection totals drawn from M1, with n_surveys surveys at every site.

    Each site is occupied with probability psi; an occupied site detects on each
    survey with probability p, an empty one never does.
    """
    occupied = rng.random(n_sites) < psi
    return rng.binomial(n_surveys, p * occupied)
