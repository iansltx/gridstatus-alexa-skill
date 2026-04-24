# Grid Status Voice Agent

This is (the start of) a tool exposing Grid Status data in a voice-in, voice-out format, using your choice of Google Dialogflow in a browser (plus some API backing) or Alexa Skills Kit (hostable entirely on ASK's built-in Lambda infrastructure).

This codebase was heavily LLM-generated; see commit history for the prompts I used. Anything without the robot emoji was me doing (minor) code edits directly.

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

`alexa/requirements.txt` lists only the ASK SDK packages that are baked into the ASK Lambda base layer; other deps were thinned before being added to the zip file to stay under ASK's 100-file 6MB limit.

### 4. Upload the code

1. Click the **Code** tab in the developer console.
2. Click the **Import Code** button in the toolbar.
3. Select `lambda_deploy.zip` and confirm the upload.
4. Click **Save** and then **Deploy**.

### 5. Store the GridStatus API key in DynamoDB

The Lambda reads its GridStatus API key from DynamoDB at cold-start via `_load_config()` in `lambda_function.py`. The key must be present before the first invocation or the function will fail to initialize.

1. Still in the **Code** tab, click **DynamoDB database**.
2. Click **Actions** > **Create item** and set the following for the new item:

   | Attribute | Type   | Value                   |
   |-----------|--------|-------------------------|
   | `id`      | String | `config`                |
   | `api_key` | String | your GridStatus API key |

   Or with the AWS CLI:

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
- A [Google Cloud service account](https://console.cloud.google.com/apis/credentials) with the **Dialogflow API Client** role enabled AND the Dialogflor API enabled on the corresponding GCP project.

### Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure the Dialogflow agent

Follow the steps in [`dialogflow/README.md`](dialogflow/README.md) to upload the `SystemOperator` entity and `CurrentEnergyMix` intent to your agent, then point the agent's webhook fulfillment URL at `https://<your-host>/hooks/dialogflow`.

### Run the server

Set the following environment variables:

| Variable                              | Description                                                             |
|---------------------------------------|-------------------------------------------------------------------------|
| `GRIDSTATUS_API_KEY`                  | Your GridStatus.io API key                                              |
| `DIALOGFLOW_PROJECT_ID`               | Your Google Cloud project ID (visible in the Dialogflow agent settings) |
| `DIALOGFLOW_SERVICE_CREDENTIALS_JSON` | Your Google Cloud service account credentials JSON file                 |

then run the app:

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

With ASK implemented, I found out that skills in development can't be demoed on the Alexa app or elsewhere...I'm pretty sure that used to work but no longer. At which point having something easily demoable was suddenly easier to do via a browser-based UI similar to my build-a-bot tutorial. So I leaned on the LLM to build me a FastAPI service to both host a small client-side implementation (basically the same as the build-a-bot tutorial, but updated to hit current API endpoints and use current versions of dependencies) and handle the web hook from Dialogflow. As part of that port, I had the LLM extract the beefy part of the response generation logic to a separate file used both by the ASK Lambda function and the Dialogflow FastAPI endpoint, so any further logic improvements would propagate to both. If I add additional intents, it may make sense to tweak this abstraction further so intent routing is handled at the vendor-independent layer, but that's en easy enough refactor when it's needed.

The final hurdle for getting that build-a-bot example working was API auth. The last time I built a voice agent the expectation was that you'd land a Dialogflow key client-side. Now, the expectation is that transactions are proxied, which makes perfect sense from a security perspective but required a new proxy endpoint on the web service. So I grabbed the proper IAM creds and told the LLM to add the endpoint.

This project reads like a proof of concept code quality wise due to time constraints (in part generated by ASK being harder to work with than expected). I also ended up leaning more on LLM package selection defaults (e.g. `pip` instead of `uv`) than I otherwise would've to make sure I got something working at all.