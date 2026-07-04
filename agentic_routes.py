"""
agentic_routes.py
------------------
Advanced I/O Flask router that receives transcribed voice commands
(English or Kannada) from VoiceCommandWidget.jsx, extracts an intent +
entities using a lightweight rule-based NLU layer, converts that into a
ZCQL query against the real Karnataka Police FIR schema, executes it, and
returns results.

SCHEMA NOTES (from Police_FIR_ER_Diagram):
  CaseMaster        - the FIR/case record. Holds FKs only (no readable text):
                        PoliceStationID -> Unit.UnitID
                        CrimeMinorHeadID -> CrimeSubHead.CrimeSubHeadID
                        CrimeMajorHeadID -> CrimeHead.CrimeHeadID
                        CaseStatusID -> CaseStatusMaster.CaseStatusID
                      Also has latitude/longitude directly - useful for maps.
  Unit              - police station; has DistrictID -> District.DistrictID
  District          - DistrictName is what a voice command actually says
  CrimeSubHead      - CrimeHeadName column here is the SUB-head name
                       (e.g. "Murder", "Robbery") despite the column name -
                       this is what most crime-type voice queries resolve to
  CrimeHead         - CrimeGroupName is the MAJOR head (broader grouping)
  CaseStatusMaster  - CaseStatusName (e.g. "Under Investigation", "Closed")
  Accused           - one row per accused person per case (CaseMasterID FK)

CONFIRM BEFORE DEMO: the exact string values seeded in CrimeSubHead.CrimeHeadName
and District.DistrictName (e.g. is it "Bengaluru" or "Bengaluru Urban"?) -
the keyword maps below are best-guess canonical values and should be checked
against the actual lookup table data.

ZCQL JOIN LIMIT: Catalyst allows a maximum of 4 JOIN clauses per query, one
condition each. Queries below are built to only add the joins a given
request actually needs, to stay under that cap.

Wiring into main.py:
    from routers.agentic_routes import agentic_bp
    app.register_blueprint(agentic_bp)
"""

import re
from flask import Blueprint, request, jsonify

try:
    import zcatalyst_sdk
except ImportError:  # allows local linting/testing without the SDK installed
    zcatalyst_sdk = None

agentic_bp = Blueprint("agentic_routes", __name__)

# --- Bilingual keyword maps --------------------------------------------------
# Canonical values on the right must match CrimeSubHead.CrimeHeadName exactly.
# Extend/correct these once the real lookup table contents are confirmed.

CRIME_TYPE_KEYWORDS = {
    "Theft": ["theft", "stolen", "steal", "ಕಳ್ಳತನ"],
    "Robbery": ["robbery", "robbed", "ದರೋಡೆ"],
    "Assault": ["assault", "attack", "ಹಲ್ಲೆ"],
    "Murder": ["murder", "killing", "homicide", "ಕೊಲೆ"],
    "Cyber Crime": ["cybercrime", "cyber crime", "online fraud", "hacking", "ಸೈಬರ್"],
    "Chain Snatching": ["chain snatching", "snatched", "ಸರಗಳ್ಳತನ"],
    "Kidnapping": ["kidnap", "abduction", "ಅಪಹರಣ"],
}

# Canonical values on the right must match District.DistrictName exactly.
DISTRICT_KEYWORDS = {
    "Bengaluru": ["bengaluru", "bangalore", "ಬೆಂಗಳೂರು"],
    "Mysuru": ["mysuru", "mysore", "ಮೈಸೂರು"],
    "Mangaluru": ["mangaluru", "mangalore", "ಮಂಗಳೂರು"],
    "Hubballi": ["hubballi", "hubli", "ಹುಬ್ಬಳ್ಳಿ"],
    "Belagavi": ["belagavi", "belgaum", "ಬೆಳಗಾವಿ"],
    "Kalaburagi": ["kalaburagi", "gulbarga", "ಕಲಬುರಗಿ"],
    "Tumakuru": ["tumakuru", "tumkur", "ಟುಮಕುರು"],
}

# Canonical values on the right must match CaseStatusMaster.CaseStatusName.
CASE_STATUS_KEYWORDS = {
    "Under Investigation": ["under investigation", "pending", "ಬಾಕಿ"],
    "Charge Sheeted": ["charge sheeted", "chargesheeted", "ಆರೋಪಪಟ್ಟಿ"],
    "Closed": ["closed", "resolved", "ಮುಚ್ಚಲಾಗಿದೆ"],
}

TIME_RANGE_KEYWORDS = {
    "today": ["today", "ಇಂದು"],
    "yesterday": ["yesterday", "ನಿನ್ನೆ"],
    "last week": ["last week", "past week", "ಕಳೆದ ವಾರ"],
    "last month": ["last month", "past month", "ಕಳೆದ ತಿಂಗಳು"],
    "this year": ["this year", "ಈ ವರ್ಷ"],
}

INTENT_KEYWORDS = {
    "list_incidents": ["show", "list", "find", "display", "ತೋರಿಸಿ", "ಪಟ್ಟಿ"],
    "hotspot_lookup": ["hotspot", "hot spot", "cluster", "ಹಾಟ್‌ಸ್ಪಾಟ್"],
    "offender_lookup": ["offender", "repeat offender", "accused", "ಆರೋಪಿ"],
}


def _match_keyword(text, keyword_map):
    text_lower = text.lower()
    for canonical, keywords in keyword_map.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return canonical
    return None


def parse_command(transcript):
    """
    Rule-based NLU: extracts intent + filters from a raw transcript.
    Returns { intent, filters: { crime_type, district, case_status, time_range } }

    Kept deterministic and simple by design. If Zia (or another NLU service)
    is confirmed available, swap this function's internals only - keep the
    same return shape so downstream query-building and the frontend don't
    need to change.
    """
    intent = _match_keyword(transcript, INTENT_KEYWORDS) or "list_incidents"
    crime_type = _match_keyword(transcript, CRIME_TYPE_KEYWORDS)
    district = _match_keyword(transcript, DISTRICT_KEYWORDS)
    case_status = _match_keyword(transcript, CASE_STATUS_KEYWORDS)
    time_range = _match_keyword(transcript, TIME_RANGE_KEYWORDS)

    return {
        "intent": intent,
        "filters": {
            "crime_type": crime_type,
            "district": district,
            "case_status": case_status,
            "time_range": time_range,
        },
    }


def _time_range_to_sql_condition(time_range, date_column="CaseMaster.CrimeRegisteredDate"):
    mapping = {
        "today": f"{date_column} = CURDATE()",
        "yesterday": f"{date_column} = DATE_SUB(CURDATE(), INTERVAL 1 DAY)",
        "last week": f"{date_column} >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        "last month": f"{date_column} >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)",
        "this year": f"YEAR({date_column}) = YEAR(CURDATE())",
    }
    return mapping.get(time_range)


def _sanitize(value):
    """Strips characters that have no business being in a ZCQL string literal."""
    return re.sub(r"[^a-zA-Z0-9\s\-]", "", value)


def build_list_incidents_query(filters):
    """
    Builds the FIR listing query. Always joins Unit + District (to resolve
    district names) and CrimeSubHead (to resolve crime type names).
    Adds CaseStatusMaster only if a status filter was detected, to respect
    ZCQL's 4-join cap (3 base joins + 1 optional = 4, right at the limit).
    """
    select_cols = [
        "CaseMaster.CaseMasterID",
        "CaseMaster.CrimeNo",
        "CaseMaster.CrimeRegisteredDate",
        "CaseMaster.latitude",
        "CaseMaster.longitude",
        "CrimeSubHead.CrimeHeadName AS CrimeType",
        "District.DistrictName AS District",
        "Unit.UnitName AS Station",
    ]

    joins = [
        "INNER JOIN Unit ON CaseMaster.PoliceStationID = Unit.UnitID",
        "INNER JOIN District ON Unit.DistrictID = District.DistrictID",
        "INNER JOIN CrimeSubHead ON CaseMaster.CrimeMinorHeadID = CrimeSubHead.CrimeSubHeadID",
    ]

    conditions = []

    if filters.get("crime_type"):
        conditions.append(f"CrimeSubHead.CrimeHeadName = '{_sanitize(filters['crime_type'])}'")
    if filters.get("district"):
        conditions.append(f"District.DistrictName = '{_sanitize(filters['district'])}'")
    if filters.get("time_range"):
        time_clause = _time_range_to_sql_condition(filters["time_range"])
        if time_clause:
            conditions.append(time_clause)

    if filters.get("case_status"):
        select_cols.append("CaseStatusMaster.CaseStatusName AS Status")
        joins.append(
            "INNER JOIN CaseStatusMaster ON CaseMaster.CaseStatusID = CaseStatusMaster.CaseStatusID"
        )
        conditions.append(
            f"CaseStatusMaster.CaseStatusName = '{_sanitize(filters['case_status'])}'"
        )

    query = f"SELECT {', '.join(select_cols)} FROM CaseMaster " + " ".join(joins)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " LIMIT 100"
    return query


def build_offender_lookup_query(filters):
    """
    Repeat-offender style query: counts how many cases each accused person
    is linked to, optionally scoped by district/crime type. Useful for
    "show repeat offenders in <district>" style voice commands.

    Joins: Accused + CaseMaster (base) + Unit + District (+ CrimeSubHead if
    a crime type filter is present) = up to 3 joins, within the cap.
    """
    joins = [
        "INNER JOIN CaseMaster ON Accused.CaseMasterID = CaseMaster.CaseMasterID",
        "INNER JOIN Unit ON CaseMaster.PoliceStationID = Unit.UnitID",
        "INNER JOIN District ON Unit.DistrictID = District.DistrictID",
    ]
    conditions = []

    if filters.get("district"):
        conditions.append(f"District.DistrictName = '{_sanitize(filters['district'])}'")

    if filters.get("crime_type"):
        joins.append(
            "INNER JOIN CrimeSubHead ON CaseMaster.CrimeMinorHeadID = CrimeSubHead.CrimeSubHeadID"
        )
        conditions.append(f"CrimeSubHead.CrimeHeadName = '{_sanitize(filters['crime_type'])}'")

    query = (
        "SELECT Accused.AccusedName, COUNT(Accused.CaseMasterID) AS CaseCount "
        "FROM Accused " + " ".join(joins)
    )
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY Accused.AccusedName HAVING COUNT(Accused.CaseMasterID) > 1"
    query += " LIMIT 50"
    return query


def build_zcql_query(parsed):
    intent = parsed["intent"]
    filters = parsed["filters"]

    if intent == "offender_lookup":
        return build_offender_lookup_query(filters)
    # hotspot_lookup uses the same shape as list_incidents (lat/long +
    # district come back either way); trend_routes.py does the heavier
    # spatial clustering math on top of this raw data.
    return build_list_incidents_query(filters)


@agentic_bp.route("/agent/voice-query", methods=["POST"])
def voice_query():
    body = request.get_json(silent=True) or {}
    transcript = (body.get("transcript") or "").strip()
    language = body.get("language", "en-IN")

    if not transcript:
        return jsonify({"error": "No transcript provided"}), 400

    parsed = parse_command(transcript)
    zcql_query = build_zcql_query(parsed)

    try:
        if zcatalyst_sdk is None:
            raise RuntimeError("zcatalyst_sdk not available in this environment")

        catalyst_app = zcatalyst_sdk.initialize(request)
        zcql = catalyst_app.zcql()
        rows = zcql.execute_query(zcql_query)
        # ZCQL join results typically come back keyed per source table, e.g.
        # [{"CaseMaster": {...}, "CrimeSubHead": {...}, "District": {...}}, ...]
        # Flatten them into a single dict per row for the frontend's convenience.
        results = [_flatten_row(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - surfaced to caller for debugging
        return jsonify({
            "intent": parsed["intent"],
            "filters": parsed["filters"],
            "query": zcql_query,
            "error": f"Query execution failed: {str(exc)}",
            "language": language,
        }), 500

    return jsonify({
        "intent": parsed["intent"],
        "filters": parsed["filters"],
        "query": zcql_query,
        "count": len(results),
        "results": results,
        "language": language,
    })


def _flatten_row(row):
    """Merges a ZCQL multi-table row (dict of dicts) into one flat dict."""
    if not isinstance(row, dict):
        return row
    flat = {}
    for table_data in row.values():
        if isinstance(table_data, dict):
            flat.update(table_data)
    return flat or row


@agentic_bp.route("/agent/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "router": "agentic_routes"})
