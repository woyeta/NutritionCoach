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
| `search_products(query)` | Packaged products by name or barcode: nutrients per 100 g, Nutri-Score, NOVA processing group, allergens from the label | [Open Food Facts](https://world.openfoodfacts.org) (no key); if its search is busy, USDA branded foods |
| `search_foods(query)` | Basic foods (e.g. "banana"): nutrients per 100 g |

Nutrients (per 100 g, when the database has them): calories, protein, fat, saturated fat, carbohydrates,
sugars, fiber, sodium, potassium, calcium, iron, magnesium, vitamin C and vitamin D. The NOVA group runs
from 1 (unprocessed or minimally processed) to 4 (ultra-processed). Allergens come from Open Food Facts'
"contains" and "may contain" label data; USDA results have none. [USDA FoodData Central](https://fdc.nal.usda.gov) (free key) |
| `calculate_daily_needs(...)` | Calorie target plus sugar, saturated fat, sodium, fiber and protein guidelines for adults | Python (Mifflin-St Jeor, WHO limits) |
| `scale_to_portion(per_100g, grams, food)` | Nutrients in a portion | Python |
| `percent_of_limit(amount, limit, label)` | What percent of a daily limit an amount is | Python |

### Your profile

Click **Profile** to save your sex, age, height, weight, activity level, goal, diet, allergies and
health conditions. The agent then uses them automatically: "What should my daily calories be?" is
answered from your profile without typing your details, while values you give in the question take
priority. Allergies are also checked against the label data of every product the agent looks up, and
matches are listed under "Allergy check against your profile" at the end of the answer.

The profile is saved only in your browser (localStorage) and sent with each question. The server
uses it for that question and never stores or logs it. It is defined in [`user_profile.py`](user_profile.py).

### Checks on every step

Each step of the loop ([`react_engine.py`](react_engine.py)) is checked before anything runs:

- **Schema validation**: arguments are checked against each tool's pydantic model ([`nutrition_coach.py`](nutrition_coach.py)), e.g. ages under 18 are rejected. Errors are sent back to the model so it can fix the call.
- **Guards**: a step limit, loop detection (a repeated identical call gets a warning, a second repeat ends the run), and a consecutive-error limit.
- **Busy databases**: requests that get "busy" responses (429/503) are retried twice. If Open Food Facts' name search is still unavailable, the app searches USDA's branded foods instead. Results are cached in memory, which also keeps the app under Open Food Facts' search rate limit.
- **Final-answer checks**: an answer is sent back once if it has a number that is not in any tool result (or the question), or if it claims a product is free of an allergen or safe for an allergy (for example "nut-free" or "contains no gluten"). Crowd-sourced allergen data can be incomplete, so the app never makes that claim. If the model does it again, the answer is shown with a warning (status `unverified`).
- **Safety**: answers are for adults and end with a short not-medical-advice note, plus a "check the package label" note when the question or answer is about allergens. The model is told to refer medical conditions, pregnancy and eating disorders to a doctor or dietitian.

See [SAMPLE_QUESTIONS.md](SAMPLE_QUESTIONS.md) for more questions and test scenarios, with expected results.

## Project structure

```
app.py              Flask app: web page and POST /api/ask
react_engine.py     The ReAct loop and its guards
nutrition_coach.py  Tools, their argument schemas, and the grounding check
tool_schema.py      Argument validation and repeated-call detection
agent_format.py     Builds the prompt for each step
json_utils.py       Extracts JSON from model output
model_loader.py     Loads Gemma 4 from Hugging Face in 4-bit
user_profile.py     The saved profile: validation, prompt line, allergy check
prompts.py          System prompt, error messages, settings
templates/, static/ Web page
tests/              Tests (no GPU or network needed)
SAMPLE_QUESTIONS.md Sample questions and scenarios for demos and manual testing
colab.ipynb         Run the app on a free Colab GPU
kaggle.ipynb        Run the app on a free Kaggle GPU
```

## Running on Google Colab (recommended)

The model needs an NVIDIA GPU, so the easiest way to run it is Colab's free T4.

1. Push this repo to GitHub.
2. Open [`colab.ipynb`](colab.ipynb) in Colab (File → Open notebook → GitHub) and choose Runtime → Change runtime type → **T4 GPU**.
3. In the Secrets panel (key icon), add `NGROK_AUTH_TOKEN` (free from ngrok) and, optionally, `FDC_API_KEY` (free from [api.data.gov](https://api.data.gov/signup/)). Turn on notebook access for both.
4. Set `REPO_URL` in the first cell to your repo, then run both cells.
5. Open the printed link. The first question takes a few minutes while the model downloads.

## Running on Kaggle

Kaggle's free notebooks also have T4 GPUs (phone verification needed).

1. Push this repo to GitHub.
2. At kaggle.com/code, create a notebook and choose File → Import notebook → [`kaggle.ipynb`](kaggle.ipynb).
3. In Session options, set Accelerator to **GPU T4 x2** and turn **Internet** on.
4. In Add-ons → Secrets, add `NGROK_AUTH_TOKEN` (and optionally `FDC_API_KEY`) and tick them for the notebook.
5. Check `REPO_URL` in the first cell, then run both cells and open the printed link.
6. Stop the session when you're done.

## Running locally

Needs Python 3.10+ and an NVIDIA GPU with CUDA.

```bash
pip install -r requirements.txt
python app.py            # then open http://localhost:5000
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_ID` | `google/gemma-4-E4B-it` | Any Gemma 4 instruct model, e.g. `google/gemma-4-E2B-it` (smaller and faster, but makes more formatting mistakes) |
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
