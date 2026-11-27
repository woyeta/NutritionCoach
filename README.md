# Nutrition Coach

A small web app that answers food and nutrition questions with a Gemma 4 model running a
Reasoning + Action (ReAct) loop. The model decides which tool to call, reads the result, and
repeats until it can answer. Every number in an answer has to come from a tool.

Example questions:

- "How much sugar is in a 30 g serving of Nutella?"
- "Compare the protein in cooked brown rice and cooked white rice per 100 g."
- "I'm a 30-year-old woman, 62 kg, 165 cm, moderately active, and want to lose weight. What should my daily calories be?"

## How it works

| Tool | What it does | Source |
|---|---|---|
| `search_products(query)` | Packaged products by name or barcode: Nutri-Score and nutrients per 100 g | [Open Food Facts](https://world.openfoodfacts.org) (no key); if its search is busy, USDA branded foods |
| `search_foods(query)` | Basic foods (e.g. "banana"): nutrients per 100 g | [USDA FoodData Central](https://fdc.nal.usda.gov) (free key) |
| `calculate_daily_needs(...)` | Calorie target plus sugar, saturated fat, sodium, fiber and protein guidelines for adults | Python (Mifflin-St Jeor, WHO limits) |
| `scale_to_portion(per_100g, grams)` | Nutrients in a portion | Python |

Each step of the loop ([`react_engine.py`](react_engine.py)) is checked before anything runs:

- **Schema validation**: arguments are checked against each tool's pydantic model ([`nutrition_coach.py`](nutrition_coach.py)), e.g. ages under 18 are rejected. Errors are sent back to the model so it can fix the call.
- **Guards**: a step limit, loop detection (a repeated identical call gets a warning, a second repeat ends the run), and a consecutive-error limit.
- **Busy databases**: requests that get "busy" responses (429/503) are retried twice. If Open Food Facts' name search is still unavailable, the app searches USDA's branded foods instead. Results are cached in memory, which also keeps the app under Open Food Facts' search rate limit.
- **Grounding check**: a final answer with a number that is not in any tool result (or the question) is sent back once. If the model does it again, the answer is shown with a warning (status `unverified`).
- **Safety**: answers are for adults and end with a short not-medical-advice note. The model is told to refer medical conditions, pregnancy and eating disorders to a doctor or dietitian.

## Project structure

```
app.py              Flask app: web page and POST /api/ask
react_engine.py     The ReAct loop and its guards
nutrition_coach.py  Tools, their argument schemas, and the grounding check
tool_schema.py      Argument validation and repeated-call detection
agent_format.py     Builds the prompt for each step
json_utils.py       Extracts JSON from model output
model_loader.py     Loads Gemma 4 from Hugging Face in 4-bit
prompts.py          System prompt, error messages, settings
templates/, static/ Web page
tests/              Tests (no GPU or network needed)
colab.ipynb         Run the app on a free Colab GPU
```

## Running on Google Colab (recommended)

The model needs an NVIDIA GPU, so the easiest way to run it is Colab's free T4.

1. Push this repo to GitHub.
2. Open [`colab.ipynb`](colab.ipynb) in Colab (File → Open notebook → GitHub) and choose Runtime → Change runtime type → **T4 GPU**.
3. In the Secrets panel (key icon), add `NGROK_AUTH_TOKEN` (free from ngrok) and, optionally, `FDC_API_KEY` (free from [api.data.gov](https://api.data.gov/signup/)). Turn on notebook access for both.
4. Set `REPO_URL` in the first cell to your repo, then run both cells.
5. Open the printed link. The first question takes a few minutes while the model downloads.

## Running locally

Needs Python 3.10+ and an NVIDIA GPU with CUDA.

```bash
pip install -r requirements.txt
python app.py            # then open http://localhost:5000
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_ID` | `google/gemma-4-E2B-it` | Any Gemma 4 instruct model, e.g. `google/gemma-4-E4B-it` (larger, more accurate) |
| `FDC_API_KEY` | `DEMO_KEY` | USDA key; the demo key is rate-limited |
| `PORT` | `5000` | Web server port |
| `FLASK_DEBUG` | `false` | Flask debug mode |

## API

```bash
curl -X POST http://localhost:5000/api/ask -H "Content-Type: application/json" \
  -d '{"query": "How much sugar is in a 30 g serving of Nutella?"}'
```

Returns `final_answer`, every step (thought, action, arguments, observation, errors), and a `status`
(`success`, `unverified`, `loop_detected`, `consecutive_errors` or `max_turns_reached`).

## Tests

```bash
python -m pytest
```

The tests replace the model and the food databases with fakes.

## Credits

Built from [AgentLoop](https://github.com/woyeta/AgentLoop). Food data from Open Food Facts
(Open Database License) and USDA FoodData Central (public domain).

General nutrition information only, not medical advice.
