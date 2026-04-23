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

