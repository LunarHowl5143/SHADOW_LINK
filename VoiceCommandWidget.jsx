import React, { useState, useRef, useCallback, useEffect } from "react";

/**
 * VoiceCommandWidget
 * ------------------
 * Captures voice input (English or Kannada) using the native Web Speech API,
 * sends the transcribed text to the backend Agentic Routes endpoint
 * (functions/crime_api_handler/routers/agentic_routes.py), and renders the
 * structured response (intent + query result) back to the investigator.
 *
 * NOTE ON BACKEND URL:
 * Replace CATALYST_AGENT_ENDPOINT below with your actual deployed Advanced I/O
 * function URL. Typical pattern for Catalyst Advanced I/O functions:
 *   https://<your-project>.catalystserverless.in/server/crime_api_handler/agent/voice-query
 * If you're using the Catalyst Web SDK client-side, you can alternatively call
 * this via `catalyst.functions().get('crime_api_handler').execute(...)`.
 */

const CATALYST_AGENT_ENDPOINT =
  process.env.REACT_APP_AGENT_ENDPOINT ||
  "/server/crime_api_handler/agent/voice-query";

const LANGUAGES = [
  { code: "en-IN", label: "English" },
  { code: "kn-IN", label: "ಕನ್ನಡ (Kannada)" },
];

const VoiceCommandWidget = () => {
  const [language, setLanguage] = useState("en-IN");
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [status, setStatus] = useState("idle"); // idle | listening | processing | done | error
  const [agentResponse, setAgentResponse] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");

  const recognitionRef = useRef(null);

  const SpeechRecognitionAPI =
    typeof window !== "undefined"
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null;

  const isSupported = Boolean(SpeechRecognitionAPI);

  const sendToAgent = useCallback(
    async (finalText) => {
      if (!finalText || !finalText.trim()) return;
      setStatus("processing");
      setErrorMsg("");
      try {
        const res = await fetch(CATALYST_AGENT_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            transcript: finalText,
            language,
          }),
        });

        if (!res.ok) {
          throw new Error(`Agent request failed with status ${res.status}`);
        }

        const data = await res.json();
        setAgentResponse(data);
        setStatus("done");
      } catch (err) {
        console.error("VoiceCommandWidget: agent request failed", err);
        setErrorMsg(
          "Could not reach the intelligence agent. Please check the connection and try again."
        );
        setStatus("error");
      }
    },
    [language]
  );

  const startListening = useCallback(() => {
    if (!isSupported) {
      setErrorMsg("Speech recognition is not supported in this browser.");
      return;
    }

    const recognition = new SpeechRecognitionAPI();
    recognition.lang = language;
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setIsListening(true);
      setStatus("listening");
      setErrorMsg("");
      setTranscript("");
      setInterimTranscript("");
      setAgentResponse(null);
    };

    recognition.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += chunk;
        } else {
          interim += chunk;
        }
      }
      if (final) {
        setTranscript((prev) => (prev ? `${prev} ${final}` : final));
      }
      setInterimTranscript(interim);
    };

    recognition.onerror = (event) => {
      console.error("Speech recognition error:", event.error);
      setErrorMsg(`Recognition error: ${event.error}`);
      setStatus("error");
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
      setInterimTranscript("");
      // Grab whatever we accumulated and send it off
      setTranscript((current) => {
        if (current && current.trim()) {
          sendToAgent(current);
        } else {
          setStatus("idle");
        }
        return current;
      });
    };

    recognitionRef.current = recognition;
    recognition.start();
  }, [SpeechRecognitionAPI, isSupported, language, sendToAgent]);

  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
    }
  }, []);

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
    };
  }, []);

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h3 style={styles.title}>🎙️ Voice Command</h3>
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          disabled={isListening}
          style={styles.select}
        >
          {LANGUAGES.map((lng) => (
            <option key={lng.code} value={lng.code}>
              {lng.label}
            </option>
          ))}
        </select>
      </div>

      {!isSupported && (
        <p style={styles.warning}>
          Your browser does not support the Web Speech API. Try Chrome or
          Edge.
        </p>
      )}

      <button
        onClick={isListening ? stopListening : startListening}
        disabled={!isSupported || status === "processing"}
        style={{
          ...styles.micButton,
          ...(isListening ? styles.micButtonActive : {}),
        }}
      >
        {isListening ? "⏹ Stop" : "🎤 Speak a command"}
      </button>

      <div style={styles.transcriptBox}>
        <p style={styles.transcriptLabel}>Transcript:</p>
        <p style={styles.transcriptText}>
          {transcript}
          <span style={styles.interimText}> {interimTranscript}</span>
          {!transcript && !interimTranscript && (
            <span style={styles.placeholder}>
              e.g. "Show theft cases in Mysuru district last week"
            </span>
          )}
        </p>
      </div>

      {status === "processing" && (
        <p style={styles.statusText}>Parsing command and querying records…</p>
      )}

      {errorMsg && <p style={styles.error}>{errorMsg}</p>}

      {agentResponse && (
        <div style={styles.resultBox}>
          <p style={styles.resultLabel}>
            Detected intent:{" "}
            <strong>{agentResponse.intent || "unknown"}</strong>
          </p>
          {agentResponse.filters && (
            <pre style={styles.filtersPre}>
              {JSON.stringify(agentResponse.filters, null, 2)}
            </pre>
          )}
          <p style={styles.resultLabel}>
            Records found: {agentResponse.count ?? "-"}
          </p>
        </div>
      )}
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
    maxWidth: 420,
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 12,
  },
  title: { margin: 0, fontSize: 16 },
  select: {
    background: "#1f2937",
    color: "#e5e7eb",
    border: "1px solid #374151",
    borderRadius: 6,
    padding: "4px 8px",
    fontSize: 13,
  },
  micButton: {
    width: "100%",
    padding: "10px 14px",
    borderRadius: 8,
    border: "none",
    background: "#2563eb",
    color: "#fff",
    fontSize: 14,
    cursor: "pointer",
    marginBottom: 12,
  },
  micButtonActive: {
    background: "#dc2626",
    animation: "pulse 1.5s infinite",
  },
  transcriptBox: {
    background: "#0b1220",
    borderRadius: 8,
    padding: 10,
    minHeight: 48,
    marginBottom: 8,
  },
  transcriptLabel: { fontSize: 11, color: "#9ca3af", margin: "0 0 4px 0" },
  transcriptText: { fontSize: 14, margin: 0, lineHeight: 1.4 },
  interimText: { color: "#9ca3af", fontStyle: "italic" },
  placeholder: { color: "#6b7280", fontStyle: "italic" },
  statusText: { fontSize: 12, color: "#93c5fd" },
  error: { fontSize: 12, color: "#f87171" },
  resultBox: {
    marginTop: 8,
    background: "#0b1220",
    borderRadius: 8,
    padding: 10,
  },
  resultLabel: { fontSize: 13, margin: "4px 0" },
  filtersPre: {
    fontSize: 11,
    background: "#111827",
    padding: 8,
    borderRadius: 6,
    overflowX: "auto",
  },
};

export default VoiceCommandWidget;
