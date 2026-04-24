## Running via Alexa Skills Kit

### 1. Create an Alexa-hosted Python skill

Log in to the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask) and create a new skill:

- **Skill name:** Grid Status (or any name you prefer)
- **Primary locale:** English (US)
- **Model:** Custom
- **Hosting:** Alexa-Hosted (Python)

The console automatically provisions a Lambda function and a DynamoDB table for the skill. Keep the browser tab open — you'll return to it in later steps.

### 2. Import the interaction model

1. Click the **Build** tab.
2. In the left sidebar, click **Interaction Model** → **JSON Editor**.
3. Replace the entire contents of the editor with the contents of `alexa/interactionModels/custom/en-US.json`.
4. Click **Save Model**, then **Build Model** and wait for the build to complete.

### 3. Build the deployment package

`lambda.zip` contains pre-bundled copies of `backports.zoneinfo` and `tzdata` (including only the American timezone data used by this skill). These packages cannot be installed on the fly inside an Alexa-hosted Lambda, so they must be shipped in the deployment ZIP alongside the application code.

Add the four application source files and `alexa/requirements.txt` into the existing zip under the same `lambda/` prefix:

```bash
cp lambda.zip lambda_deploy.zip

mkdir -p _stage/lambda
cp lambda_function.py api.py gridstatus_lite.py energy_mix_intent.py _stage/lambda/
cp alexa/requirements.txt _stage/lambda/requirements.txt

cd _stage && zip -r ../lambda_deploy.zip lambda/ && cd ..
rm -rf _stage
```

`alexa/requirements.txt` lists only the ASK SDK packages. The timezone packages are already in the zip and must **not** be listed there — the Alexa-hosted Lambda environment has strict limits on non-AWS package installations, and re-installing them would exceed those limits.

### 4. Upload the code

1. Click the **Code** tab in the developer console.
2. Click the **three-dot menu (⋯)** next to your skill name at the top of the file tree and choose **Import Code**.
3. Select `lambda_deploy.zip` and confirm the upload.
4. Click **Save** and then **Deploy**.

### 5. Store the GridStatus API key in DynamoDB

The Lambda reads its GridStatus API key from DynamoDB at cold-start via `_load_config()` in `lambda_function.py`. The key must be present before the first invocation or the function will fail to initialise.

1. Still in the **Code** tab, click **AWS Console** (top-right corner) to open the skill's Lambda in the AWS console.
2. Under **Configuration → Environment variables**, note the values of `DYNAMODB_PERSISTENCE_REGION` and `DYNAMODB_PERSISTENCE_TABLE_NAME`.
3. Navigate to **DynamoDB** in the AWS console (ensure you are in the correct region) and open that table.
4. Add an item with the following attributes:

   | Attribute | Type   | Value                   |
   |-----------|--------|-------------------------|
   | `id`      | String | `config`                |
   | `api_key` | String | your GridStatus API key |

   Or with the AWS CLI:

   ```bash
   aws dynamodb put-item \
     --region <DYNAMODB_PERSISTENCE_REGION> \
     --table-name <DYNAMODB_PERSISTENCE_TABLE_NAME> \
     --item '{"id": {"S": "config"}, "api_key": {"S": "<your_gridstatus_api_key>"}}'
   ```

### 6. Test the skill

Click the **Test** tab in the developer console and set the skill stage to **Development**. Try:

> "Alexa, open Grid Status"

> "What is the fuel mix for ERCOT right now?"

> "What was the generation mix in California at 3 PM?"

## Running via Dialogflow/FastAPI

### Prerequisites

- Python 3.11+
- A [GridStatus.io API key](https://www.gridstatus.io/)
- A [Dialogflow ES agent](https://dialogflow.cloud.google.com/) with the `SystemOperator` entity and `CurrentEnergyMix` intent uploaded (see [`dialogflow/README.md`](dialogflow/README.md) for instructions)
- A [Google Cloud API key](https://console.cloud.google.com/apis/credentials) with the Dialogflow API enabled, scoped to your project

### Environment variables

| Variable | Description |
|---|---|
| `GRIDSTATUS_API_KEY` | Your GridStatus.io API key |
| `DIALOGFLOW_PROJECT_ID` | Your Google Cloud project ID (visible in the Dialogflow agent settings) |
| `DIALOGFLOW_API_KEY` | Your Google Cloud API key with Dialogflow API access |

Copy these into a `.env` file in the project root:

```
GRIDSTATUS_API_KEY=your_gridstatus_key
DIALOGFLOW_PROJECT_ID=your_gcp_project_id
DIALOGFLOW_API_KEY=your_gcp_api_key
```

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure the Dialogflow agent

Follow the steps in [`dialogflow/README.md`](dialogflow/README.md) to upload the `SystemOperator` entity and `CurrentEnergyMix` intent to your agent, then point the agent's webhook fulfillment URL at `https://<your-host>/hooks/dialogflow`.

### Run the server

```bash
uvicorn main:app --reload
```

The chat UI will be available at `http://localhost:8000`. The Dialogflow webhook endpoint is at `http://localhost:8000/hooks/dialogflow`.

For exposing your local server to Dialogflow during development, a tunnelling tool such as [ngrok](https://ngrok.com/) is useful:

```bash
ngrok http 8000
# then set the webhook URL in Dialogflow to https://<your-ngrok-id>.ngrok-free.app/hooks/dialogflow
```

## Decision log

The general idea: build a skill/action baked into an existing voice agent platform (so folks don't have to install a completely different app, nor go out of their way in a workflow) to pull various pieces of Grid Status information. How much data to expose is basically dictated by "I'm going to see how far I can get in six hours, starting from scratch," starting with easier things like current/historical energy usage/fuel mix and then moving on to items that require persistence like "what are energy prices like at the resource node closest to me."

Initially I planned to build a Google Assistant Action using Dialogflow, as I've used those pieces in the past (https://slides.com/ianlittman/build-a-bot-world-17, which was based off of a project I did for a client). Turns out, conversational actions got sunset a few years ago (https://developers.google.com/assistant/ca-sunset), so I pivoted to the other voice agent platform I've used before: Alexa Skills Kit (https://developer.amazon.com/en-US/alexa/alexa-skills-kit).

Started off with the Python-based "high-low" game Alexa skill template since I want to be able to persist my location manually as an end user at some point in the exercise. This template also allowed for leaning on preset AWS infrastructure at a built-in free tier rather than needing to worry about setting infrastructure on my own, leaving more time to do dev (and the ability to leave the skill up after completing the assignment without worrying about bills).

My initial "Intent" was to get current and historical ISO and BA fuel mix. This required click-ops'ing through potential "slot" values for the ISO/BA. ISOs were easy enough. For BAs, I grabbed the EIA area list from a browser-based API response, then noramlized things slightly via

```php
<?php

$data = json_decode(file_get_contents('ba.json'), true)['data'];

foreach (array_slice($data, 1) as $row) {
    if ($row[2] !== 'balancing authority') {
        continue;
    }

    $cleanedName = str_replace([', Inc.', ', LLC'], '', $row[1]);
    $cleanedName = str_replace(' - ', ', ', $cleanedName);

    fputcsv(STDOUT, [$row[0], '', $cleanedName], escape: '');    
}
```

Then did some manual tweaks on top of that to match how someone might say the BA name.

After setting up the intent/slot values, I switched over to building the Lambda function. Which, the first steps of building were nuking irrelevant code from the example while keeping relevant code. After getting to a hard-coded initial state where the intent I had set up was being routed correctly, I flipped to "ask the bot to make a rough attempt at the build" mode, as I figured that Alexa API conventions are baked into model training sets at this point.

The next catch was that the Alexa-hosted Lambdas have a pretty strict limit for included files, and merely including `requirements.txt` isn't enough to have dependencies installed on the fly. AWS SDK components are exempt from this limit, but anything else I included wouldn't be, and things like `pandas` or `requests` are unwieldy. If I were doing this a year ago I'd go back to the drawing board on where to host this work (next attempt probably would've been a proper "normal" Lambda function), but I wanted to see whether I could get the LLM to extract the relevant functionality (I only needed `get_dataset()` in this case) so that I could provide those functions with zero dependencies for this particular case, while keeping the same interface back to the code that had already been written. That way, when switching from the trivial-to-host Lambda implementation to a better one, it'd just be a matter of adding the Grid Status library dependency in and throwing away the custom code.

With that issue out of the way, the next significant hurdle was, of course, time zones. Data in the data set was delivered in UTC, which makes sense, but that's not what someone is thinking of when requesting energy mix. Things get trickier for BAs/ISOs spanning multiple time zones, but we can indicate which time zone we picked as part of the response to clarify, and let the end user revise what time they used. It took a minute to notice the issue because the speech response was speaking in present tense rather than past tense for old records.