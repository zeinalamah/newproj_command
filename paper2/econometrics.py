#!/usr/bin/env python3
"""Small, transparent econometric utilities used by the Paper 2 build.

The estimators deliberately avoid hidden state.  Multi-way fixed effects are
absorbed by alternating projections; standard errors are one-way cluster robust.
The routines are intended for diagnostic and replication work, not as a general
replacement for established econometric packages.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class FEResult:
    terms: list[str]
    beta: np.ndarray
    se: np.ndarray
    pvalue: np.ndarray
    vcov: np.ndarray
    nobs: int
    clusters: int
    r2_within: float
    iterations: int
    converged: bool

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "term": self.terms,
            "coefficient": self.beta,
            "std_error": self.se,
            "p_value": self.pvalue,
            "n_obs": self.nobs,
            "clusters": self.clusters,
            "r2_within": self.r2_within,
            "fe_iterations": self.iterations,
            "fe_converged": self.converged,
        })


def _factorize(series: pd.Series) -> np.ndarray:
    return pd.factorize(series, sort=False)[0].astype(np.int64)


def _group_demean(values: np.ndarray, codes: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    """Return group means expanded back to observations."""
    n_groups = int(codes.max()) + 1 if len(codes) else 0
    if values.ndim == 1:
        values = values[:, None]
    if weights is None:
        denom = np.bincount(codes, minlength=n_groups).astype(float)
        out = np.empty_like(values, dtype=float)
        for j in range(values.shape[1]):
            numer = np.bincount(codes, weights=values[:, j], minlength=n_groups)
            means = np.divide(numer, denom, out=np.zeros_like(numer), where=denom > 0)
            out[:, j] = means[codes]
    else:
        denom = np.bincount(codes, weights=weights, minlength=n_groups).astype(float)
        out = np.empty_like(values, dtype=float)
        for j in range(values.shape[1]):
            numer = np.bincount(codes, weights=weights * values[:, j], minlength=n_groups)
            means = np.divide(numer, denom, out=np.zeros_like(numer), where=denom > 0)
            out[:, j] = means[codes]
    return out


def absorb_fixed_effects(
    matrix: np.ndarray,
    groups: Sequence[pd.Series | np.ndarray],
    weights: np.ndarray | None = None,
    tolerance: float = 1e-10,
    max_iterations: int = 500,
) -> tuple[np.ndarray, int, bool]:
    """Absorb an arbitrary number of additive fixed effects.

    Alternating weighted projections converge to the within transformation.  The
    returned matrix has the same shape as the input.
    """
    z = np.asarray(matrix, dtype=float).copy()
    if z.ndim == 1:
        z = z[:, None]
    codes = [g if isinstance(g, np.ndarray) and np.issubdtype(g.dtype, np.integer) else _factorize(pd.Series(g)) for g in groups]
    scale = max(1.0, float(np.nanmax(np.abs(z))))
    converged = False
    for iteration in range(1, max_iterations + 1):
        before = z.copy()
        for code in codes:
            z -= _group_demean(z, code, weights)
        change = float(np.nanmax(np.abs(z - before)))
        if change <= tolerance * scale:
            converged = True
            break
    return z, iteration, converged


def fe_ols(
    data: pd.DataFrame,
    y: str,
    x: Sequence[str],
    fixed_effects: Sequence[str],
    cluster: str,
    weight: str | None = None,
    drop_singletons: bool = True,
) -> FEResult:
    """OLS after absorbing fixed effects, with CRV1 cluster-robust inference."""
    columns = [y, *x, *fixed_effects, cluster] + ([weight] if weight else [])
    frame = data[columns].replace([np.inf, -np.inf], np.nan).dropna().copy()
    if drop_singletons:
        changed = True
        while changed and len(frame):
            changed = False
            for fixed_effect in fixed_effects:
                counts = frame[fixed_effect].value_counts()
                keep = frame[fixed_effect].map(counts).gt(1)
                if not bool(keep.all()):
                    frame = frame.loc[keep].copy()
                    changed = True
    if len(frame) <= len(x) + 2:
        raise ValueError("Insufficient observations after cleaning and singleton removal")
    weights = None if weight is None else frame[weight].to_numpy(float)
    if weights is not None:
        weights = np.clip(weights, 1e-12, np.inf)
    raw = frame[[y, *x]].to_numpy(float)
    transformed, iterations, converged = absorb_fixed_effects(
        raw,
        [frame[column] for column in fixed_effects],
        weights=weights,
    )
    yw = transformed[:, 0]
    Xw = transformed[:, 1:]
    if weights is not None:
        root = np.sqrt(weights)
        yw = yw * root
        Xw = Xw * root[:, None]
    rank = np.linalg.matrix_rank(Xw)
    if rank < Xw.shape[1]:
        keep = np.array([np.linalg.norm(Xw[:, j]) > 1e-12 for j in range(Xw.shape[1])])
        if keep.sum() < Xw.shape[1]:
            Xw = Xw[:, keep]
            x = [term for term, include in zip(x, keep) if include]
    xtx_inv = np.linalg.pinv(Xw.T @ Xw)
    beta = xtx_inv @ (Xw.T @ yw)
    residual = yw - Xw @ beta
    cluster_codes = _factorize(frame[cluster])
    n_clusters = int(cluster_codes.max()) + 1
    scores = np.zeros((n_clusters, Xw.shape[1]))
    for group in range(n_clusters):
        mask = cluster_codes == group
        scores[group] = Xw[mask].T @ residual[mask]
    meat = scores.T @ scores
    nobs = len(frame)
    k = Xw.shape[1]
    correction = 1.0
    if n_clusters > 1 and nobs > k:
        correction = (n_clusters / (n_clusters - 1)) * ((nobs - 1) / (nobs - k))
    vcov = correction * xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.clip(np.diag(vcov), 0, np.inf))
    df = max(1, n_clusters - 1)
    tstat = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    pvalue = 2 * stats.t.sf(np.abs(tstat), df=df)
    centered = yw - np.average(yw)
    tss = float(centered @ centered)
    rss = float(residual @ residual)
    r2 = 1 - rss / tss if tss > 0 else np.nan
    return FEResult(list(x), beta, se, pvalue, vcov, nobs, n_clusters, r2, iterations, converged)


def wald_test(result: FEResult, terms: Iterable[str]) -> dict[str, float]:
    indices = [result.terms.index(term) for term in terms if term in result.terms]
    if not indices:
        return {"statistic": np.nan, "df": 0, "p_value": np.nan}
    beta = result.beta[indices]
    variance = result.vcov[np.ix_(indices, indices)]
    statistic = float(beta.T @ np.linalg.pinv(variance) @ beta)
    return {"statistic": statistic, "df": len(indices), "p_value": float(stats.chi2.sf(statistic, len(indices)))}


def randomization_pvalue(observed: float, placebo: np.ndarray, two_sided: bool = True) -> float:
    values = np.asarray(placebo, dtype=float)
    values = values[np.isfinite(values)]
    if two_sided:
        exceed = np.sum(np.abs(values) >= abs(observed))
    else:
        exceed = np.sum(values >= observed)
    return float((exceed + 1) / (len(values) + 1)) if len(values) else np.nan


def percentile_rank(observed: float, placebo: np.ndarray) -> float:
    values = np.asarray(placebo, dtype=float)
    values = values[np.isfinite(values)]
    return float((np.sum(values <= observed) + 0.5) / (len(values) + 1)) if len(values) else np.nan
