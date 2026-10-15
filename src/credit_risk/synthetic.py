"""Synthetic Home Credit look-alike data generator.

Purpose: integration tests and a dry-run of the full pipeline *without* the Kaggle files.
The generator reproduces the real file names, column names, column order, key relationships,
sentinels (DAYS_EMPLOYED = 365243, XNA categories) and plausible value ranges. Values are random
with a latent-risk structure so that models have signal to learn — **numbers produced from this
data are NOT findings about Home Credit and must never be reported as such.**
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

HOUSING_BASE = [
    "APARTMENTS", "BASEMENTAREA", "YEARS_BEGINEXPLUATATION", "YEARS_BUILD", "COMMONAREA", "ELEVATORS",
    "ENTRANCES", "FLOORSMAX", "FLOORSMIN", "LANDAREA", "LIVINGAPARTMENTS", "LIVINGAREA",
    "NONLIVINGAPARTMENTS", "NONLIVINGAREA",
]
HOUSING_COLS = [f"{b}_{s}" for s in ("AVG", "MODE", "MEDI") for b in HOUSING_BASE]

APPLICATION_COLUMNS = (
    ["SK_ID_CURR", "TARGET", "NAME_CONTRACT_TYPE", "CODE_GENDER", "FLAG_OWN_CAR", "FLAG_OWN_REALTY",
     "CNT_CHILDREN", "AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY", "AMT_GOODS_PRICE", "NAME_TYPE_SUITE",
     "NAME_INCOME_TYPE", "NAME_EDUCATION_TYPE", "NAME_FAMILY_STATUS", "NAME_HOUSING_TYPE",
     "REGION_POPULATION_RELATIVE", "DAYS_BIRTH", "DAYS_EMPLOYED", "DAYS_REGISTRATION", "DAYS_ID_PUBLISH",
     "OWN_CAR_AGE", "FLAG_MOBIL", "FLAG_EMP_PHONE", "FLAG_WORK_PHONE", "FLAG_CONT_MOBILE", "FLAG_PHONE",
     "FLAG_EMAIL", "OCCUPATION_TYPE", "CNT_FAM_MEMBERS", "REGION_RATING_CLIENT", "REGION_RATING_CLIENT_W_CITY",
     "WEEKDAY_APPR_PROCESS_START", "HOUR_APPR_PROCESS_START", "REG_REGION_NOT_LIVE_REGION",
     "REG_REGION_NOT_WORK_REGION", "LIVE_REGION_NOT_WORK_REGION", "REG_CITY_NOT_LIVE_CITY",
     "REG_CITY_NOT_WORK_CITY", "LIVE_CITY_NOT_WORK_CITY", "ORGANIZATION_TYPE", "EXT_SOURCE_1", "EXT_SOURCE_2",
     "EXT_SOURCE_3"]
    + HOUSING_COLS
    + ["FONDKAPREMONT_MODE", "HOUSETYPE_MODE", "TOTALAREA_MODE", "WALLSMATERIAL_MODE", "EMERGENCYSTATE_MODE",
       "OBS_30_CNT_SOCIAL_CIRCLE", "DEF_30_CNT_SOCIAL_CIRCLE", "OBS_60_CNT_SOCIAL_CIRCLE",
       "DEF_60_CNT_SOCIAL_CIRCLE", "DAYS_LAST_PHONE_CHANGE"]
    + [f"FLAG_DOCUMENT_{i}" for i in range(2, 22)]
    + ["AMT_REQ_CREDIT_BUREAU_HOUR", "AMT_REQ_CREDIT_BUREAU_DAY", "AMT_REQ_CREDIT_BUREAU_WEEK",
       "AMT_REQ_CREDIT_BUREAU_MON", "AMT_REQ_CREDIT_BUREAU_QRT", "AMT_REQ_CREDIT_BUREAU_YEAR"]
)

BUREAU_COLUMNS = ["SK_ID_CURR", "SK_ID_BUREAU", "CREDIT_ACTIVE", "CREDIT_CURRENCY", "DAYS_CREDIT",
                  "CREDIT_DAY_OVERDUE", "DAYS_CREDIT_ENDDATE", "DAYS_ENDDATE_FACT", "AMT_CREDIT_MAX_OVERDUE",
                  "CNT_CREDIT_PROLONG", "AMT_CREDIT_SUM", "AMT_CREDIT_SUM_DEBT", "AMT_CREDIT_SUM_LIMIT",
                  "AMT_CREDIT_SUM_OVERDUE", "CREDIT_TYPE", "DAYS_CREDIT_UPDATE", "AMT_ANNUITY"]
PREVIOUS_COLUMNS = ["SK_ID_PREV", "SK_ID_CURR", "NAME_CONTRACT_TYPE", "AMT_ANNUITY", "AMT_APPLICATION",
                    "AMT_CREDIT", "AMT_DOWN_PAYMENT", "AMT_GOODS_PRICE", "WEEKDAY_APPR_PROCESS_START",
                    "HOUR_APPR_PROCESS_START", "FLAG_LAST_APPL_PER_CONTRACT", "NFLAG_LAST_APPL_IN_DAY",
                    "RATE_DOWN_PAYMENT", "RATE_INTEREST_PRIMARY", "RATE_INTEREST_PRIVILEGED",
                    "NAME_CASH_LOAN_PURPOSE", "NAME_CONTRACT_STATUS", "DAYS_DECISION", "NAME_PAYMENT_TYPE",
                    "CODE_REJECT_REASON", "NAME_TYPE_SUITE", "NAME_CLIENT_TYPE", "NAME_GOODS_CATEGORY",
                    "NAME_PORTFOLIO", "NAME_PRODUCT_TYPE", "CHANNEL_TYPE", "SELLERPLACE_AREA",
                    "NAME_SELLER_INDUSTRY", "CNT_PAYMENT", "NAME_YIELD_GROUP", "PRODUCT_COMBINATION",
                    "DAYS_FIRST_DRAWING", "DAYS_FIRST_DUE", "DAYS_LAST_DUE_1ST_VERSION", "DAYS_LAST_DUE",
                    "DAYS_TERMINATION", "NFLAG_INSURED_ON_APPROVAL"]
POS_COLUMNS = ["SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "CNT_INSTALMENT", "CNT_INSTALMENT_FUTURE",
               "NAME_CONTRACT_STATUS", "SK_DPD", "SK_DPD_DEF"]
CC_COLUMNS = ["SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "AMT_BALANCE", "AMT_CREDIT_LIMIT_ACTUAL",
              "AMT_DRAWINGS_ATM_CURRENT", "AMT_DRAWINGS_CURRENT", "AMT_DRAWINGS_OTHER_CURRENT",
              "AMT_DRAWINGS_POS_CURRENT", "AMT_INST_MIN_REGULARITY", "AMT_PAYMENT_CURRENT",
              "AMT_PAYMENT_TOTAL_CURRENT", "AMT_RECEIVABLE_PRINCIPAL", "AMT_RECIVABLE", "AMT_TOTAL_RECEIVABLE",
              "CNT_DRAWINGS_ATM_CURRENT", "CNT_DRAWINGS_CURRENT", "CNT_DRAWINGS_OTHER_CURRENT",
              "CNT_DRAWINGS_POS_CURRENT", "CNT_INSTALMENT_MATURE_CUM", "NAME_CONTRACT_STATUS", "SK_DPD",
              "SK_DPD_DEF"]
INST_COLUMNS = ["SK_ID_PREV", "SK_ID_CURR", "NUM_INSTALMENT_VERSION", "NUM_INSTALMENT_NUMBER",
                "DAYS_INSTALMENT", "DAYS_ENTRY_PAYMENT", "AMT_INSTALMENT", "AMT_PAYMENT"]

OCCUPATIONS = ["Laborers", "Sales staff", "Core staff", "Managers", "Drivers", "High skill tech staff",
               "Accountants", "Medicine staff", "Security staff", "Cooking staff", "Cleaning staff",
               "Private service staff", "Low-skill Laborers", "Waiters/barmen staff", "Secretaries",
               "Realty agents", "HR staff", "IT staff"]
ORGANIZATIONS = ["Business Entity Type 3", "Self-employed", "Other", "Medicine", "Government",
                 "Business Entity Type 2", "School", "Trade: type 7", "Kindergarten", "Construction",
                 "Transport: type 4", "Industry: type 9", "Security", "Military", "Bank"]


def _choice(rng: np.random.Generator, values: list, n: int, p: list[float] | None = None) -> np.ndarray:
    p_arr = None if p is None else np.asarray(p, dtype=float) / np.sum(p)
    return rng.choice(np.asarray(values, dtype=object), size=n, p=p_arr)


def _with_nan(rng: np.random.Generator, arr: np.ndarray, rate: float) -> np.ndarray:
    out = arr.astype(float)
    out[rng.random(len(out)) < rate] = np.nan
    return out


def _application(rng: np.random.Generator, ids: np.ndarray, latent: np.ndarray, labeled: bool) -> pd.DataFrame:
    warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
    n = len(ids)
    df = pd.DataFrame(index=range(n))
    df["SK_ID_CURR"] = ids
    if labeled:
        p = 1 / (1 + np.exp(-(-2.75 + 1.1 * latent)))
        df["TARGET"] = (rng.random(n) < p).astype(int)
    df["NAME_CONTRACT_TYPE"] = _choice(rng, ["Cash loans", "Revolving loans"], n, [0.9, 0.1])
    df["CODE_GENDER"] = _choice(rng, ["F", "M", "XNA"], n, [0.658, 0.3418, 0.0002])
    df["FLAG_OWN_CAR"] = _choice(rng, ["N", "Y"], n, [0.66, 0.34])
    df["FLAG_OWN_REALTY"] = _choice(rng, ["Y", "N"], n, [0.69, 0.31])
    df["CNT_CHILDREN"] = rng.poisson(0.42, n)
    income = np.round(np.exp(rng.normal(11.95, 0.48, n) - 0.08 * latent) / 450) * 450
    df["AMT_INCOME_TOTAL"] = np.clip(income, 25650, 1.2e7)
    credit = np.round(np.exp(rng.normal(13.1, 0.6, n)) / 500) * 500
    df["AMT_CREDIT"] = np.clip(credit, 45000, 4.05e6)
    df["AMT_ANNUITY"] = _with_nan(rng, np.round(df["AMT_CREDIT"] / rng.uniform(10, 40, n), 1), 0.00005)
    goods = np.round(df["AMT_CREDIT"] * rng.uniform(0.8, 1.0, n), -3)
    df["AMT_GOODS_PRICE"] = np.where(rng.random(n) < 0.001, np.nan, goods)
    df["NAME_TYPE_SUITE"] = _choice(rng, ["Unaccompanied", "Family", "Spouse, partner", "Children", "Other_B",
                                          None], n, [0.80, 0.13, 0.037, 0.01, 0.006, 0.017])
    income_type = _choice(rng, ["Working", "Commercial associate", "Pensioner", "State servant", "Unemployed",
                                "Student"], n, [0.516, 0.233, 0.18, 0.0705, 0.0003, 0.0002])
    df["NAME_INCOME_TYPE"] = income_type
    df["NAME_EDUCATION_TYPE"] = _choice(rng, ["Secondary / secondary special", "Higher education",
                                              "Incomplete higher", "Lower secondary", "Academic degree"],
                                        n, [0.71, 0.243, 0.033, 0.0124, 0.0006])
    df["NAME_FAMILY_STATUS"] = _choice(rng, ["Married", "Single / not married", "Civil marriage", "Separated",
                                             "Widow"], n, [0.64, 0.147, 0.097, 0.064, 0.052])
    df["NAME_HOUSING_TYPE"] = _choice(rng, ["House / apartment", "With parents", "Municipal apartment",
                                            "Rented apartment", "Office apartment", "Co-op apartment"],
                                      n, [0.887, 0.048, 0.036, 0.016, 0.0085, 0.0045])
    df["REGION_POPULATION_RELATIVE"] = np.round(rng.uniform(0.0003, 0.072, n), 6)
    age_days = np.clip(rng.normal(43.9, 11.9, n) - 2.5 * latent, 21, 69) * 365.25
    df["DAYS_BIRTH"] = -np.round(age_days).astype(int)
    pensioner = income_type == "Pensioner"
    employed_days = np.minimum(rng.exponential(2400, n), age_days - 18 * 365)
    df["DAYS_EMPLOYED"] = np.where(pensioner, 365243, -np.round(np.maximum(employed_days, 0))).astype(int)
    df["DAYS_REGISTRATION"] = -np.round(rng.uniform(0, 20000, n), 0)
    df["DAYS_ID_PUBLISH"] = -np.round(rng.uniform(0, 6000, n)).astype(int)
    df["OWN_CAR_AGE"] = np.where(df["FLAG_OWN_CAR"] == "Y", rng.integers(0, 30, n), np.nan)
    df["FLAG_MOBIL"] = 1
    df["FLAG_EMP_PHONE"] = np.where(pensioner, 0, 1)
    df["FLAG_WORK_PHONE"] = (rng.random(n) < 0.2).astype(int)
    df["FLAG_CONT_MOBILE"] = (rng.random(n) < 0.998).astype(int)
    df["FLAG_PHONE"] = (rng.random(n) < 0.28).astype(int)
    df["FLAG_EMAIL"] = (rng.random(n) < 0.056).astype(int)
    occ = _choice(rng, OCCUPATIONS, n)
    occ[(rng.random(n) < 0.31) | pensioner] = None
    df["OCCUPATION_TYPE"] = occ
    df["CNT_FAM_MEMBERS"] = df["CNT_CHILDREN"] + np.where(df["NAME_FAMILY_STATUS"].isin(["Married", "Civil marriage"]), 2, 1)
    df["REGION_RATING_CLIENT"] = np.clip(np.round(2 + 0.3 * latent + rng.normal(0, 0.5, n)), 1, 3).astype(int)
    df["REGION_RATING_CLIENT_W_CITY"] = df["REGION_RATING_CLIENT"]
    df["WEEKDAY_APPR_PROCESS_START"] = _choice(rng, ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
                                                     "SATURDAY", "SUNDAY"], n)
    df["HOUR_APPR_PROCESS_START"] = rng.integers(0, 24, n)
    for col in ["REG_REGION_NOT_LIVE_REGION", "REG_REGION_NOT_WORK_REGION", "LIVE_REGION_NOT_WORK_REGION",
                "REG_CITY_NOT_LIVE_CITY", "REG_CITY_NOT_WORK_CITY", "LIVE_CITY_NOT_WORK_CITY"]:
        df[col] = (rng.random(n) < 0.1).astype(int)
    org = _choice(rng, ORGANIZATIONS, n)
    org[pensioner] = "XNA"
    df["ORGANIZATION_TYPE"] = org

    def ext(noise: float, miss: float) -> np.ndarray:
        raw = 1 / (1 + np.exp(0.9 * latent + rng.normal(0, noise, n)))
        return _with_nan(rng, np.round(np.clip(raw, 0.0001, 0.96), 6), miss)

    df["EXT_SOURCE_1"] = ext(1.0, 0.56)
    df["EXT_SOURCE_2"] = ext(0.9, 0.002)
    df["EXT_SOURCE_3"] = ext(0.9, 0.20)
    has_housing = rng.random(n) < 0.5
    for col in HOUSING_COLS:
        base = rng.uniform(0, 1, n) if not col.startswith("YEARS") else rng.uniform(0.6, 1, n)
        df[col] = np.where(has_housing & (rng.random(n) < 0.85), np.round(base, 4), np.nan)
    df["FONDKAPREMONT_MODE"] = np.where(has_housing, _choice(rng, ["reg oper account", "org spec account",
                                                                   "reg oper spec account", "not specified"], n), None)
    df["HOUSETYPE_MODE"] = np.where(has_housing, _choice(rng, ["block of flats", "specific housing",
                                                               "terraced house"], n, [0.97, 0.02, 0.01]), None)
    df["TOTALAREA_MODE"] = np.where(has_housing, np.round(rng.uniform(0, 1, n), 4), np.nan)
    df["WALLSMATERIAL_MODE"] = np.where(has_housing, _choice(rng, ["Panel", "Stone, brick", "Block", "Wooden",
                                                                   "Mixed", "Monolithic", "Others"], n), None)
    df["EMERGENCYSTATE_MODE"] = np.where(has_housing, _choice(rng, ["No", "Yes"], n, [0.985, 0.015]), None)
    obs30 = _with_nan(rng, rng.poisson(1.4, n), 0.003)
    df["OBS_30_CNT_SOCIAL_CIRCLE"] = obs30
    df["DEF_30_CNT_SOCIAL_CIRCLE"] = np.where(np.isnan(obs30), np.nan, rng.binomial(np.nan_to_num(obs30).astype(int), 0.1))
    df["OBS_60_CNT_SOCIAL_CIRCLE"] = obs30
    df["DEF_60_CNT_SOCIAL_CIRCLE"] = df["DEF_30_CNT_SOCIAL_CIRCLE"]
    df["DAYS_LAST_PHONE_CHANGE"] = -np.round(rng.exponential(900, n))
    for i in range(2, 22):
        rate = 0.71 if i == 3 else (0.08 if i in (6, 8) else 0.01)
        df[f"FLAG_DOCUMENT_{i}"] = (rng.random(n) < rate).astype(int)
    bureau_missing = rng.random(n) < 0.135
    for col, lam in [("HOUR", 0.006), ("DAY", 0.007), ("WEEK", 0.03), ("MON", 0.27), ("QRT", 0.27), ("YEAR", 1.9)]:
        df[f"AMT_REQ_CREDIT_BUREAU_{col}"] = np.where(bureau_missing, np.nan, rng.poisson(lam, n))
    cols = [c for c in APPLICATION_COLUMNS if labeled or c != "TARGET"]
    return df[cols]


def _repeat_parent(rng: np.random.Generator, parent_ids: np.ndarray, mean: float, zero_share: float) -> np.ndarray:
    counts = rng.poisson(mean, len(parent_ids))
    counts[rng.random(len(parent_ids)) < zero_share] = 0
    return np.repeat(parent_ids, counts)


def generate(out_dir: Path, n_train: int = 20_000, n_test: int = 4_000, seed: int = 7) -> dict[str, int]:
    """Write all Home Credit-shaped CSVs into out_dir and return row counts per file."""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    n_all = n_train + n_test
    ids = np.arange(100002, 100002 + n_all)
    rng.shuffle(ids)
    latent = rng.normal(0, 1, n_all)
    latent_by_id = dict(zip(ids, latent, strict=True))

    train = _application(rng, ids[:n_train], latent[:n_train], labeled=True)
    test = _application(rng, ids[n_train:], latent[n_train:], labeled=False)

    # ---- bureau (+ a few orphan customers that are not in application, like the real data)
    b_curr = _repeat_parent(rng, ids, 5.6, 0.14)
    nb = len(b_curr)
    b_lat = np.vectorize(latent_by_id.get)(b_curr)
    bureau = pd.DataFrame({
        "SK_ID_CURR": b_curr,
        "SK_ID_BUREAU": np.arange(5_000_000, 5_000_000 + nb),
        "CREDIT_ACTIVE": _choice(rng, ["Closed", "Active", "Sold", "Bad debt"], nb, [0.63, 0.365, 0.004, 0.001]),
        "CREDIT_CURRENCY": _choice(rng, ["currency 1", "currency 2"], nb, [0.999, 0.001]),
        "DAYS_CREDIT": -rng.integers(1, 2922, nb),
    })
    bureau["CREDIT_DAY_OVERDUE"] = np.where(rng.random(nb) < 0.01 + 0.02 * (b_lat > 1), rng.integers(1, 120, nb), 0)
    bureau["DAYS_CREDIT_ENDDATE"] = _with_nan(rng, bureau["DAYS_CREDIT"] + rng.integers(30, 3000, nb), 0.06)
    closed = bureau["CREDIT_ACTIVE"] == "Closed"
    fact = bureau["DAYS_CREDIT"] + rng.integers(1, 2500, nb)
    bureau["DAYS_ENDDATE_FACT"] = np.where(closed, np.minimum(fact, -1), np.nan)
    bureau["AMT_CREDIT_MAX_OVERDUE"] = _with_nan(rng, np.where(rng.random(nb) < 0.1, rng.exponential(5000, nb), 0), 0.65)
    bureau["CNT_CREDIT_PROLONG"] = (rng.random(nb) < 0.005).astype(int)
    amt = np.round(np.exp(rng.normal(11.8, 1.2, nb)), 1)
    bureau["AMT_CREDIT_SUM"] = amt
    bureau["AMT_CREDIT_SUM_DEBT"] = _with_nan(rng, np.where(closed, 0, np.round(amt * rng.uniform(0, 1, nb), 1)), 0.15)
    bureau["AMT_CREDIT_SUM_LIMIT"] = _with_nan(rng, np.where(rng.random(nb) < 0.1, amt * 0.5, 0), 0.34)
    bureau["AMT_CREDIT_SUM_OVERDUE"] = np.where(bureau["CREDIT_DAY_OVERDUE"] > 0, np.round(amt * 0.05, 1), 0)
    bureau["CREDIT_TYPE"] = _choice(rng, ["Consumer credit", "Credit card", "Car loan", "Mortgage", "Microloan"],
                                    nb, [0.73, 0.23, 0.017, 0.011, 0.007])
    bureau["DAYS_CREDIT_UPDATE"] = np.minimum(bureau["DAYS_CREDIT"] + rng.integers(0, 3000, nb), 0)
    # the real data contains a handful of post-application "update" dates -> keep a few for the leakage filter
    bureau.loc[rng.random(nb) < 0.0005, "DAYS_CREDIT_UPDATE"] = rng.integers(1, 30)
    bureau["AMT_ANNUITY"] = _with_nan(rng, np.round(amt / 30, 1), 0.7)
    bureau = bureau[BUREAU_COLUMNS]

    # ---- bureau_balance (only part of bureau has monthly history; MONTHS_BALANCE <= 0)
    bb_parent = bureau.sample(frac=0.45, random_state=seed)
    months = rng.integers(3, 30, len(bb_parent))
    bb_ids = np.repeat(bb_parent["SK_ID_BUREAU"].to_numpy(), months)
    bb_lat = np.repeat(np.vectorize(latent_by_id.get)(bb_parent["SK_ID_CURR"].to_numpy()), months)
    offsets = np.concatenate([np.arange(m) for m in months])
    nbb = len(bb_ids)
    late = rng.random(nbb) < (0.02 + 0.04 * np.clip(bb_lat, 0, None))
    status = np.where(late, _choice(rng, ["1", "2", "3", "4", "5"], nbb, [0.8, 0.1, 0.05, 0.03, 0.02]),
                      _choice(rng, ["0", "C", "X"], nbb, [0.5, 0.35, 0.15]))
    bureau_balance = pd.DataFrame({"SK_ID_BUREAU": bb_ids, "MONTHS_BALANCE": -offsets, "STATUS": status})

    # ---- previous_application
    p_curr = _repeat_parent(rng, ids, 4.6, 0.05)
    npv = len(p_curr)
    p_lat = np.vectorize(latent_by_id.get)(p_curr)
    status_p = np.where(rng.random(npv) < 0.17 + 0.06 * np.clip(p_lat, 0, None), "Refused",
                        _choice(rng, ["Approved", "Canceled", "Unused offer"], npv, [0.75, 0.23, 0.02]))
    app_amt = np.round(np.exp(rng.normal(11.3, 1.1, npv)), -2)
    prev = pd.DataFrame({
        "SK_ID_PREV": np.arange(1_000_000, 1_000_000 + npv),
        "SK_ID_CURR": p_curr,
        "NAME_CONTRACT_TYPE": _choice(rng, ["Cash loans", "Consumer loans", "Revolving loans", "XNA"], npv,
                                      [0.447, 0.437, 0.116, 0.0002]),
        "AMT_ANNUITY": _with_nan(rng, np.round(app_amt / rng.uniform(6, 36, npv), 2), 0.22),
        "AMT_APPLICATION": app_amt,
        "AMT_CREDIT": np.where(status_p == "Approved", np.round(app_amt * rng.uniform(0.9, 1.2, npv), -2), app_amt),
        "AMT_DOWN_PAYMENT": _with_nan(rng, np.round(app_amt * rng.uniform(0, 0.2, npv), -1), 0.54),
        "AMT_GOODS_PRICE": _with_nan(rng, app_amt, 0.23),
        "WEEKDAY_APPR_PROCESS_START": _choice(rng, ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
                                                    "SATURDAY", "SUNDAY"], npv),
        "HOUR_APPR_PROCESS_START": rng.integers(0, 24, npv),
        "FLAG_LAST_APPL_PER_CONTRACT": _choice(rng, ["Y", "N"], npv, [0.995, 0.005]),
        "NFLAG_LAST_APPL_IN_DAY": 1,
        "RATE_DOWN_PAYMENT": _with_nan(rng, rng.uniform(0, 0.3, npv), 0.54),
        "RATE_INTEREST_PRIMARY": _with_nan(rng, rng.uniform(0.03, 1, npv), 0.997),
        "RATE_INTEREST_PRIVILEGED": _with_nan(rng, rng.uniform(0.37, 1, npv), 0.997),
        "NAME_CASH_LOAN_PURPOSE": _choice(rng, ["XAP", "XNA", "Repairs", "Other", "Urgent needs"], npv,
                                          [0.55, 0.41, 0.015, 0.01, 0.015]),
        "NAME_CONTRACT_STATUS": status_p,
        "DAYS_DECISION": -rng.integers(1, 2923, npv),
        "NAME_PAYMENT_TYPE": _choice(rng, ["Cash through the bank", "XNA", "Non-cash from your account"], npv,
                                     [0.62, 0.375, 0.005]),
        "CODE_REJECT_REASON": np.where(status_p == "Refused", _choice(rng, ["HC", "LIMIT", "SCO", "XAP"], npv), "XAP"),
        "NAME_TYPE_SUITE": _choice(rng, ["Unaccompanied", "Family", None], npv, [0.3, 0.13, 0.57]),
        "NAME_CLIENT_TYPE": _choice(rng, ["Repeater", "New", "Refreshed"], npv, [0.74, 0.18, 0.08]),
        "NAME_GOODS_CATEGORY": _choice(rng, ["XNA", "Mobile", "Consumer Electronics", "Computers"], npv,
                                       [0.57, 0.14, 0.15, 0.14]),
        "NAME_PORTFOLIO": _choice(rng, ["POS", "Cash", "XNA", "Cards"], npv, [0.41, 0.28, 0.22, 0.09]),
        "NAME_PRODUCT_TYPE": _choice(rng, ["XNA", "x-sell", "walk-in"], npv, [0.64, 0.27, 0.09]),
        "CHANNEL_TYPE": _choice(rng, ["Credit and cash offices", "Country-wide", "Stone", "AP+ (Cash loan)"], npv),
        "SELLERPLACE_AREA": rng.integers(-1, 500, npv),
        "NAME_SELLER_INDUSTRY": _choice(rng, ["XNA", "Consumer electronics", "Connectivity"], npv, [0.51, 0.24, 0.25]),
        "CNT_PAYMENT": _with_nan(rng, rng.choice([6, 10, 12, 18, 24, 36], npv).astype(float), 0.22),
        "NAME_YIELD_GROUP": _choice(rng, ["XNA", "middle", "high", "low_normal", "low_action"], npv),
        "PRODUCT_COMBINATION": _choice(rng, ["Cash", "POS household with interest", "POS mobile with interest",
                                             "Card Street"], npv),
    })
    approved = prev["NAME_CONTRACT_STATUS"] == "Approved"
    first_due = prev["DAYS_DECISION"] + rng.integers(10, 60, npv)
    prev["DAYS_FIRST_DRAWING"] = np.where(approved, 365243, np.nan)
    prev["DAYS_FIRST_DUE"] = np.where(approved, first_due, np.nan)
    last_due = first_due + 30 * np.nan_to_num(prev["CNT_PAYMENT"], nan=12)
    prev["DAYS_LAST_DUE_1ST_VERSION"] = np.where(approved, last_due, np.nan)
    prev["DAYS_LAST_DUE"] = np.where(approved, np.where(rng.random(npv) < 0.3, 365243, last_due), np.nan)
    prev["DAYS_TERMINATION"] = np.where(approved, np.where(rng.random(npv) < 0.3, 365243, last_due + 5), np.nan)
    prev["NFLAG_INSURED_ON_APPROVAL"] = np.where(approved, (rng.random(npv) < 0.33).astype(float), np.nan)
    prev = prev[PREVIOUS_COLUMNS]

    appr = prev[approved].reset_index(drop=True)
    appr_lat = np.vectorize(latent_by_id.get)(appr["SK_ID_CURR"].to_numpy())

    # ---- POS_CASH_balance: POS / cash loans, monthly
    pos_par = appr[appr["NAME_CONTRACT_TYPE"] != "Revolving loans"]
    pos_lat = appr_lat[pos_par.index.to_numpy()]
    m = rng.integers(2, 18, len(pos_par))
    rep = np.repeat(np.arange(len(pos_par)), m)
    offs = np.concatenate([np.arange(k) for k in m])
    npos = len(rep)
    dpd = np.where(rng.random(npos) < 0.02 + 0.05 * np.clip(pos_lat[rep], 0, None), rng.integers(1, 90, npos), 0)
    cnt_inst = np.nan_to_num(pos_par["CNT_PAYMENT"].to_numpy()[rep], nan=12)
    pos = pd.DataFrame({
        "SK_ID_PREV": pos_par["SK_ID_PREV"].to_numpy()[rep],
        "SK_ID_CURR": pos_par["SK_ID_CURR"].to_numpy()[rep],
        "MONTHS_BALANCE": -(offs + rng.integers(1, 40, npos) * (offs == 0) + 1),
        "CNT_INSTALMENT": cnt_inst,
        "CNT_INSTALMENT_FUTURE": np.maximum(cnt_inst - offs, 0),
        "NAME_CONTRACT_STATUS": np.where(cnt_inst - offs <= 0, "Completed", "Active"),
        "SK_DPD": dpd,
        "SK_DPD_DEF": np.where(dpd > 30, dpd - 30, 0),
    })[POS_COLUMNS]

    # ---- credit_card_balance: revolving products
    cc_par = appr[appr["NAME_CONTRACT_TYPE"] == "Revolving loans"]
    cc_lat = appr_lat[cc_par.index.to_numpy()]
    m = rng.integers(3, 24, len(cc_par))
    rep = np.repeat(np.arange(len(cc_par)), m)
    offs = np.concatenate([np.arange(k) for k in m])
    ncc = len(rep)
    limit = np.repeat(rng.choice([45000, 90000, 135000, 180000, 270000], len(cc_par)), m).astype(float)
    util = np.clip(rng.beta(1.2, 3, ncc) + 0.15 * np.clip(cc_lat[rep], 0, None), 0, 1.2)
    bal = np.round(limit * util, 2)
    draw = np.round(rng.exponential(8000, ncc) * (rng.random(ncc) < 0.4), 2)
    ccdpd = np.where(rng.random(ncc) < 0.01 + 0.04 * np.clip(cc_lat[rep], 0, None), rng.integers(1, 60, ncc), 0)
    cc = pd.DataFrame({
        "SK_ID_PREV": cc_par["SK_ID_PREV"].to_numpy()[rep],
        "SK_ID_CURR": cc_par["SK_ID_CURR"].to_numpy()[rep],
        "MONTHS_BALANCE": -(offs + 1),
        "AMT_BALANCE": bal,
        "AMT_CREDIT_LIMIT_ACTUAL": limit,
        "AMT_DRAWINGS_ATM_CURRENT": _with_nan(rng, draw * 0.6, 0.2),
        "AMT_DRAWINGS_CURRENT": draw,
        "AMT_DRAWINGS_OTHER_CURRENT": _with_nan(rng, np.zeros(ncc), 0.2),
        "AMT_DRAWINGS_POS_CURRENT": _with_nan(rng, draw * 0.4, 0.2),
        "AMT_INST_MIN_REGULARITY": _with_nan(rng, np.round(bal * 0.05, 2), 0.08),
        "AMT_PAYMENT_CURRENT": _with_nan(rng, np.round(bal * rng.uniform(0.02, 0.3, ncc), 2), 0.2),
        "AMT_PAYMENT_TOTAL_CURRENT": np.round(bal * rng.uniform(0.0, 0.3, ncc), 2),
        "AMT_RECEIVABLE_PRINCIPAL": np.round(bal * 0.95, 2),
        "AMT_RECIVABLE": bal,
        "AMT_TOTAL_RECEIVABLE": bal,
        "CNT_DRAWINGS_ATM_CURRENT": _with_nan(rng, rng.poisson(0.3, ncc), 0.2),
        "CNT_DRAWINGS_CURRENT": rng.poisson(0.7, ncc),
        "CNT_DRAWINGS_OTHER_CURRENT": _with_nan(rng, np.zeros(ncc), 0.2),
        "CNT_DRAWINGS_POS_CURRENT": _with_nan(rng, rng.poisson(0.4, ncc), 0.2),
        "CNT_INSTALMENT_MATURE_CUM": _with_nan(rng, offs.astype(float), 0.08),
        "NAME_CONTRACT_STATUS": _choice(rng, ["Active", "Completed", "Signed"], ncc, [0.96, 0.03, 0.01]),
        "SK_DPD": ccdpd,
        "SK_DPD_DEF": np.where(ccdpd > 30, 1, 0),
    })[CC_COLUMNS]

    # ---- installments_payments: scheduled vs paid, some partial payments split in 2 rows
    inst_par = pos_par
    inst_lat = pos_lat
    k = rng.integers(2, 14, len(inst_par))
    rep = np.repeat(np.arange(len(inst_par)), k)
    num = np.concatenate([np.arange(1, x + 1) for x in k])
    ni = len(rep)
    due = (inst_par["DAYS_DECISION"].to_numpy()[rep] + 30 * num).astype(float)
    due = np.minimum(due, -1)
    late_days = np.where(rng.random(ni) < 0.07 + 0.08 * np.clip(inst_lat[rep], 0, None),
                         rng.integers(1, 60, ni), -rng.integers(0, 15, ni))
    amt_i = np.round(np.nan_to_num(inst_par["AMT_ANNUITY"].to_numpy()[rep], nan=5000.0), 3)
    under = rng.random(ni) < 0.04 + 0.04 * np.clip(inst_lat[rep], 0, None)
    paid = np.where(under, np.round(amt_i * rng.uniform(0.2, 0.95, ni), 3), amt_i)
    inst = pd.DataFrame({
        "SK_ID_PREV": inst_par["SK_ID_PREV"].to_numpy()[rep],
        "SK_ID_CURR": inst_par["SK_ID_CURR"].to_numpy()[rep],
        "NUM_INSTALMENT_VERSION": np.where(num == 1, 1.0, rng.choice([0.0, 1.0], ni, p=[0.3, 0.7])),
        "NUM_INSTALMENT_NUMBER": num,
        "DAYS_INSTALMENT": due,
        "DAYS_ENTRY_PAYMENT": _with_nan(rng, np.minimum(due + late_days, -1), 0.0002),
        "AMT_INSTALMENT": amt_i,
        "AMT_PAYMENT": paid,
    })
    # split ~3% of payments into two rows (partial payments on the same instalment)
    split_mask = rng.random(ni) < 0.03
    extra = inst[split_mask].copy()
    inst.loc[split_mask, "AMT_PAYMENT"] = np.round(inst.loc[split_mask, "AMT_PAYMENT"] / 2, 3)
    extra["AMT_PAYMENT"] = inst.loc[split_mask, "AMT_PAYMENT"].to_numpy()
    extra["DAYS_ENTRY_PAYMENT"] = extra["DAYS_ENTRY_PAYMENT"] - 3
    inst = pd.concat([inst, extra], ignore_index=True)[INST_COLUMNS]

    # ---- column description file (latin-1, unnamed index column like the original)
    desc_rows = []
    for table, cols in [("application_{train|test}.csv", APPLICATION_COLUMNS), ("bureau.csv", BUREAU_COLUMNS),
                        ("bureau_balance.csv", ["SK_ID_BUREAU", "MONTHS_BALANCE", "STATUS"]),
                        ("POS_CASH_balance.csv", POS_COLUMNS), ("credit_card_balance.csv", CC_COLUMNS),
                        ("previous_application.csv", PREVIOUS_COLUMNS),
                        ("installments_payments.csv", INST_COLUMNS)]:
        for c in cols:
            desc_rows.append({"Table": table, "Row": c, "Description": f"[synthetic] description of {c}",
                              "Special": "hashed" if c.startswith("SK_ID") else ""})
    desc = pd.DataFrame(desc_rows)
    desc.index = desc.index + 1

    files = {
        "application_train.csv": train, "application_test.csv": test, "bureau.csv": bureau,
        "bureau_balance.csv": bureau_balance, "previous_application.csv": prev, "POS_CASH_balance.csv": pos,
        "credit_card_balance.csv": cc, "installments_payments.csv": inst,
    }
    counts = {}
    for name, frame in files.items():
        frame.to_csv(out_dir / name, index=False)
        counts[name] = len(frame)
    desc.to_csv(out_dir / "HomeCredit_columns_description.csv", encoding="latin-1")
    counts["HomeCredit_columns_description.csv"] = len(desc)
    (out_dir / "SYNTHETIC_DATA_README.txt").write_text(
        "These files are SYNTHETIC look-alikes generated by credit_risk.synthetic for testing.\n"
        "They are not Home Credit data. Do not report any number derived from them as a finding.\n",
        encoding="utf-8",
    )
    return counts
