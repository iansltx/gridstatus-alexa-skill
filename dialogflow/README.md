# Dialogflow ES — Import Files

This directory contains intent and entity definitions in the Dialogflow ES
export format, ready to be uploaded to an existing Dialogflow ES agent.

## Directory layout

```
dialogflow/
├── entities/
│   └── SystemOperator.json   # Custom entity with all ISO / BA codes
└── intents/
    └── CurrentEnergyMix.json # Intent definition with parameters, webhook
                              # flag, and English training phrases
```

---

## Uploading the entity

1. Open your Dialogflow ES agent in the console
   (<https://dialogflow.cloud.google.com/>).
2. Click **Entities** in the left sidebar.
3. Click the **⋮ (three-dot)** menu at the top right of the Entities list and
   choose **Upload entity**.
4. Select `entities/SystemOperator.json` and confirm.

The `SystemOperator` entity maps canonical ISO / Balancing-Authority codes
(e.g. `ERCOT`, `CAISO`) to common spoken synonyms (e.g. "Texas",
"California").  Upload this **before** uploading the intent so that the
`@SystemOperator` parameter type resolves correctly.

---

## Uploading the intent

1. Click **Intents** in the left sidebar.
2. Click the **⋮ (three-dot)** menu at the top right of the Intents list and
   choose **Upload intent**.
3. Select `intents/CurrentEnergyMix.json` and confirm.

`CurrentEnergyMix.json` is a single self-contained file that includes both
the intent definition (parameters, webhook flag, etc.) and the English
training phrases under the `userSays` key.  No ZIP archive is required.

---

## Post-upload configuration

After uploading:

1. **Enable webhook fulfillment for the agent** — go to
   **Fulfillment → Webhook**, enable it, and set the URL to wherever your
   FastAPI server is reachable, e.g.:

   ```
   https://your-server.example.com/hooks/dialogflow
   ```

2. Open the **CurrentEnergyMix** intent, scroll to **Fulfillment**, and make
   sure **Enable webhook call for this intent** is toggled on.  (The uploaded
   JSON already sets `"webhookUsed": true`, but it is worth verifying in the
   UI.)

3. Train the agent (**Train** button, top right) after any changes.

---

## Parameter mapping

| Dialogflow parameter | Entity type       | Alexa slot equivalent |
|----------------------|-------------------|-----------------------|
| `iso`                | `@SystemOperator` | `iso` (`SystemOperator`) |
| `time`               | `@sys.time`       | `time` (`AMAZON.TIME`) |
| `date`               | `@sys.date`       | `date` (`AMAZON.DATE`) |

Dialogflow delivers `@sys.time` and `@sys.date` as full ISO-8601 timestamps.
The webhook (`main.py`) strips them down to `HH:MM` and `YYYY-MM-DD`
respectively before passing them to the shared handler in
`energy_mix_intent.py`.