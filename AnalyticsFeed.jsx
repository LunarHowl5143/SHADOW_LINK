import React, { useEffect, useState, useCallback, useRef } from "react";

/**
 * AnalyticsFeed
 * -------------
 * Polls the backend Trend Routes endpoint
 * (functions/crime_api_handler/routers/trend_routes.py) for detected
 * anomalies / emerging trends and renders them as a live feed.
 * High-severity anomalies get a pulsing red-zone indicator, matching the
 * "Emerging Trend Alerts" requirement from the problem statement.
 *
 * Replace CATALYST_TRENDS_ENDPOINT with your deployed function URL.
 */

const CATALYST_TRENDS_ENDPOINT =
  process.env.REACT_APP_TRENDS_ENDPOINT ||
  "/server/crime_api_handler/trends/anomalies";

const POLL_INTERVAL_MS = 30000; // 30s refresh

const severityColor = {
  high: "#dc2626",
  medium: "#f59e0b",
  low: "#3b82f6",
};

const AnalyticsFeed = () => {
  const [anomalies, setAnomalies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);
  const pollRef = useRef(null);

  const fetchAnomalies = useCallback(async () => {
    try {
      setError("");
      const res = await fetch(CATALYST_TRENDS_ENDPOINT);
      if (!res.ok) throw new Error(`Request failed: ${res.status}`);
      const data = await res.json();
      setAnomalies(Array.isArray(data.anomalies) ? data.anomalies : []);
      setLastUpdated(new Date());
    } catch (err) {
      console.error("AnalyticsFeed: failed to fetch anomalies", err);
      setError("Unable to load trend data right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAnomalies();
    pollRef.current = setInterval(fetchAnomalies, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [fetchAnomalies]);

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h3 style={styles.title}>📈 Trend Anomaly Feed</h3>
        <button onClick={fetchAnomalies} style={styles.refreshBtn}>
          Refresh
        </button>
      </div>

      {lastUpdated && (
        <p style={styles.timestamp}>
          Last updated: {lastUpdated.toLocaleTimeString()}
        </p>
      )}

      {loading && <p style={styles.info}>Loading trend data…</p>}
      {error && <p style={styles.error}>{error}</p>}
      {!loading && !error && anomalies.length === 0 && (
        <p style={styles.info}>No significant anomalies detected currently.</p>
      )}

      <ul style={styles.list}>
        {anomalies.map((item, idx) => (
          <li
            key={item.id || idx}
            style={{
              ...styles.item,
              borderLeft: `4px solid ${
                severityColor[item.severity] || "#6b7280"
              }`,
            }}
          >
            <div style={styles.itemTop}>
              <span
                style={{
                  ...styles.badge,
                  background: severityColor[item.severity] || "#6b7280",
                  ...(item.severity === "high" ? styles.pulsingBadge : {}),
                }}
              >
                {(item.severity || "info").toUpperCase()}
              </span>
              <span style={styles.crimeType}>{item.crime_type}</span>
            </div>
            <p style={styles.itemDesc}>
              {item.district}
              {item.station ? ` — ${item.station}` : ""}: {item.description}
            </p>
            <p style={styles.itemMeta}>
              Observed: {item.observed_count} vs. baseline avg{" "}
              {item.baseline_avg} ({item.deviation_pct > 0 ? "+" : ""}
              {item.deviation_pct}%)
            </p>
          </li>
        ))}
      </ul>

      <style>{`
        @keyframes pulseGlow {
          0% { box-shadow: 0 0 0 0 rgba(220,38,38,0.6); }
          70% { box-shadow: 0 0 0 8px rgba(220,38,38,0); }
          100% { box-shadow: 0 0 0 0 rgba(220,38,38,0); }
        }
      `}</style>
    </div>
  );
};

const styles = {
  container: {
    background: "#111827",
    border: "1px solid #1f2937",
    borderRadius: 12,
    padding: 16,
    color: "#e5e7eb",
    fontFamily: "Inter, sans-serif",
    maxWidth: 480,
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: { margin: 0, fontSize: 16 },
  refreshBtn: {
    background: "#1f2937",
    color: "#e5e7eb",
    border: "1px solid #374151",
    borderRadius: 6,
    padding: "4px 10px",
    fontSize: 12,
    cursor: "pointer",
  },
  timestamp: { fontSize: 11, color: "#6b7280", margin: "4px 0 10px" },
  info: { fontSize: 13, color: "#9ca3af" },
  error: { fontSize: 13, color: "#f87171" },
  list: { listStyle: "none", padding: 0, margin: 0 },
  item: {
    background: "#0b1220",
    borderRadius: 6,
    padding: "10px 12px",
    marginBottom: 8,
  },
  itemTop: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  badge: {
    fontSize: 10,
    fontWeight: 700,
    color: "#fff",
    borderRadius: 4,
    padding: "2px 6px",
  },
  pulsingBadge: {
    animation: "pulseGlow 1.6s infinite",
  },
  crimeType: { fontSize: 13, fontWeight: 600 },
  itemDesc: { fontSize: 13, margin: "2px 0" },
  itemMeta: { fontSize: 11, color: "#9ca3af", margin: 0 },
};

export default AnalyticsFeed;
