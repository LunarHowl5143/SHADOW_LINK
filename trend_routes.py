"""
trend_routes.py
----------------
Advanced I/O Flask router responsible for:
  1. GET /trends/anomalies                 -> statistical anomaly detection
                                               feeding AnalyticsFeed.jsx
  2. POST /trends/text-analyze              -> text analytics adapter (Zia if
                                               available, rule-based fallback)
  3. GET /trends/predictive-risk-scores     -> predictive "high-risk area"
                                               scoring; also cron-friendly

SCHEMA NOTES (from Police_FIR_ER_Diagram) - see agentic_routes.py for the
full rundown. The columns this file actually touches:
  CaseMaster.CaseMasterID, CrimeRegisteredDate, latitude, longitude,
  PoliceStationID -> Unit.UnitID -> Unit.DistrictID -> District.DistrictName
  CrimeMinorHeadID -> CrimeSubHead.CrimeSubHeadID -> CrimeSubHead.CrimeHeadName

ZCQL JOIN LIMIT: max 4 JOIN clauses per query, one condition each. The
grouped-count query below uses exactly 3 (Unit, District, CrimeSubHead),
leaving headroom for a status filter if you add one later.

Wiring into main.py:
    from routers.trend_routes import trend_bp
    app.register_blueprint(trend_bp)

CRON WIRING (if time permits):
Configure a Catalyst Cron Job (via the Catalyst console) to hit this same
Advanced I/O function at `/trends/predictive-risk-scores?trigger=cron` on a
schedule (e.g. nightly), so risk scores refresh automatically.
"""

from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

try:
    import zcatalyst_sdk
except ImportError:
    zcatalyst_sdk = None

trend_bp = Blueprint("trend_routes", __name__)

ANOMALY_THRESHOLD_PCT = 40
HIGH_SEVERITY_THRESHOLD_PCT = 80

RECENT_WINDOW_DAYS = 7
BASELINE_WINDOW_DAYS = 90


def _get_zcql():
    if zcatalyst_sdk is None:
        raise RuntimeError("zcatalyst_sdk not available in this environment")
    catalyst_app = zcatalyst_sdk.initialize(request)
    return catalyst_app.zcql()


GROUPED_COUNT_QUERY = """
    SELECT District.DistrictName, CrimeSubHead.CrimeHeadName, Unit.UnitName,
           COUNT(CaseMaster.CaseMasterID) AS CaseCount
    FROM CaseMaster
    INNER JOIN Unit ON CaseMaster.PoliceStationID = Unit.UnitID
    INNER JOIN District ON Unit.DistrictID = District.DistrictID
    INNER JOIN CrimeSubHead ON CaseMaster.CrimeMinorHeadID = CrimeSubHead.CrimeSubHeadID
    WHERE CaseMaster.CrimeRegisteredDate >= '{date_from}'
      AND CaseMaster.CrimeRegisteredDate < '{date_to}'
    GROUP BY District.DistrictName, CrimeSubHead.CrimeHeadName, Unit.UnitName
"""


def _fetch_group_counts(zcql, date_from, date_to):
    """
    Returns counts keyed by (district, crime_type, station) for the given
    date range, using CrimeRegisteredDate as the anchor date field.
    """
    query = GROUPED_COUNT_QUERY.format(date_from=date_from, date_to=date_to)
    rows = zcql.execute_query(query)

    result = {}
    for row in rows:
        flat = {}
        for table_data in row.values():
            if isinstance(table_data, dict):
                flat.update(table_data)
        key = (flat.get("DistrictName"), flat.get("CrimeHeadName"), flat.get("UnitName"))
        result[key] = int(flat.get("CaseCount", 0))
    return result


def detect_anomalies():
    """
    Compares the recent window's FIR counts (per district/crime-type/station)
    against a historical daily baseline average over a longer window, and
    flags significant positive deviations as anomalies.
    """
    zcql = _get_zcql()

    today = datetime.utcnow().date()
    recent_from = today - timedelta(days=RECENT_WINDOW_DAYS)
    baseline_from = today - timedelta(days=BASELINE_WINDOW_DAYS)

    recent_counts = _fetch_group_counts(zcql, recent_from.isoformat(), today.isoformat())
    baseline_counts = _fetch_group_counts(zcql, baseline_from.isoformat(), recent_from.isoformat())

    baseline_days = max(BASELINE_WINDOW_DAYS - RECENT_WINDOW_DAYS, 1)

    anomalies = []
    for key, recent_cnt in recent_counts.items():
        district, crime_type, station = key
        baseline_total = baseline_counts.get(key, 0)
        baseline_avg_scaled = (baseline_total / baseline_days) * RECENT_WINDOW_DAYS

        if baseline_avg_scaled <= 0:
            if recent_cnt >= 3:
                anomalies.append(_make_anomaly(
                    district, crime_type, station, recent_cnt, 0, 100, "medium",
                    "New activity with no historical baseline"
                ))
            continue

        deviation_pct = round(((recent_cnt - baseline_avg_scaled) / baseline_avg_scaled) * 100, 1)

        if deviation_pct >= ANOMALY_THRESHOLD_PCT:
            severity = "high" if deviation_pct >= HIGH_SEVERITY_THRESHOLD_PCT else "medium"
            anomalies.append(_make_anomaly(
                district, crime_type, station, recent_cnt,
                round(baseline_avg_scaled, 1), deviation_pct, severity,
                f"{crime_type} FIRs up {deviation_pct}% vs. historical average"
            ))

    anomalies.sort(key=lambda a: a["deviation_pct"], reverse=True)
    return anomalies


def _make_anomaly(district, crime_type, station, observed, baseline_avg, deviation_pct, severity, description):
    return {
        "id": f"{district}-{crime_type}-{station}-{datetime.utcnow().date().isoformat()}",
        "district": district,
        "station": station,
        "crime_type": crime_type,
        "observed_count": observed,
        "baseline_avg": baseline_avg,
        "deviation_pct": deviation_pct,
        "severity": severity,
        "description": description,
    }


@trend_bp.route("/trends/anomalies", methods=["GET"])
def get_anomalies():
    try:
        anomalies = detect_anomalies()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Anomaly detection failed: {str(exc)}", "anomalies": []}), 500

    return jsonify({"anomalies": anomalies, "generated_at": datetime.utcnow().isoformat()})


# --- Geospatial hotspot helper (feeds the map layer other operators build) --

HOTSPOT_QUERY = """
    SELECT CaseMaster.latitude, CaseMaster.longitude, District.DistrictName,
           CrimeSubHead.CrimeHeadName
    FROM CaseMaster
    INNER JOIN Unit ON CaseMaster.PoliceStationID = Unit.UnitID
    INNER JOIN District ON Unit.DistrictID = District.DistrictID
    INNER JOIN CrimeSubHead ON CaseMaster.CrimeMinorHeadID = CrimeSubHead.CrimeSubHeadID
    WHERE CaseMaster.CrimeRegisteredDate >= '{date_from}'
      AND CaseMaster.latitude IS NOT NULL AND CaseMaster.longitude IS NOT NULL
    LIMIT 300
"""


@trend_bp.route("/trends/hotspot-points", methods=["GET"])
def hotspot_points():
    """
    Returns raw lat/long points (with district + crime type) for the recent
    window, for the map/visualization layer to cluster client-side or feed
    into a heatmap library. Kept separate from /trends/anomalies since this
    is point data, not aggregated stats.
    """
    days = int(request.args.get("days", RECENT_WINDOW_DAYS))
    date_from = (datetime.utcnow().date() - timedelta(days=days)).isoformat()

    try:
        zcql = _get_zcql()
        rows = zcql.execute_query(HOTSPOT_QUERY.format(date_from=date_from))
        points = []
        for row in rows:
            flat = {}
            for table_data in row.values():
                if isinstance(table_data, dict):
                    flat.update(table_data)
            if flat.get("latitude") and flat.get("longitude"):
                points.append({
                    "lat": float(flat["latitude"]),
                    "lng": float(flat["longitude"]),
                    "district": flat.get("DistrictName"),
                    "crime_type": flat.get("CrimeHeadName"),
                })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Hotspot fetch failed: {str(exc)}", "points": []}), 500

    return jsonify({"points": points, "generated_at": datetime.utcnow().isoformat()})


# --- Text analytics (Zia adapter with rule-based fallback) -----------------

def analyze_text_with_zia(text):
    """
    Attempts Catalyst Zia's text analytics (e.g. keyword extraction) on
    free-text fields like CaseMaster.BriefFacts, if the SDK exposes it in
    this environment. Falls back to a simple rule-based extractor otherwise.

    CONFIRM: exact Zia method name/signature against the current Catalyst
    Python SDK docs before relying on this for the demo - this varies by
    SDK version and plan.
    """
    if zcatalyst_sdk is not None:
        try:
            catalyst_app = zcatalyst_sdk.initialize(request)
            zia = catalyst_app.zia()
            keywords = zia.keyword_extraction(text)  # placeholder call
            return {"source": "zia", "keywords": keywords}
        except Exception:
            pass

    return {"source": "rule_based", "keywords": _rule_based_keywords(text)}


def _rule_based_keywords(text):
    known_terms = ["theft", "robbery", "assault", "murder", "cyber", "fraud", "snatching", "kidnap"]
    text_lower = text.lower()
    return [term for term in known_terms if term in text_lower]


@trend_bp.route("/trends/text-analyze", methods=["POST"])
def text_analyze():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400
    return jsonify(analyze_text_with_zia(text))


# --- Predictive risk scoring (manual trigger + cron-friendly) --------------

def compute_predictive_risk_scores():
    """
    Ranks (district, station) pairs by a weighted combination of anomaly
    severity and deviation, as a stand-in "risk score" until a proper ML
    model is plugged in. Swap the scoring formula later without changing
    the endpoint contract.
    """
    anomalies = detect_anomalies()

    scores = {}
    for a in anomalies:
        key = (a["district"], a["station"])
        weight = {"high": 3, "medium": 2, "low": 1}.get(a["severity"], 1)
        scores[key] = scores.get(key, 0) + weight * (1 + a["deviation_pct"] / 100)

    ranked = [
        {"district": d, "station": s, "risk_score": round(score, 2)}
        for (d, s), score in scores.items()
    ]
    ranked.sort(key=lambda r: r["risk_score"], reverse=True)
    return ranked


@trend_bp.route("/trends/predictive-risk-scores", methods=["GET"])
def predictive_risk_scores():
    triggered_by_cron = request.args.get("trigger") == "cron"
    try:
        scores = compute_predictive_risk_scores()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Risk scoring failed: {str(exc)}"}), 500

    response = {
        "risk_scores": scores,
        "generated_at": datetime.utcnow().isoformat(),
        "triggered_by": "cron" if triggered_by_cron else "manual",
    }
    # TODO: if triggered_by_cron, persist `scores` to a RiskScores table
    # (schema TBD) so the dashboard reads cached scores instead of recomputing.
    return jsonify(response)


@trend_bp.route("/trends/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "router": "trend_routes"})
