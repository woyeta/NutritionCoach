"""
Loads a Gemma 4 instruct model from Hugging Face in 4-bit and generates responses.
"""

import logging
import threading
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from agent_format import Generation, build_messages
from prompts import MAX_NEW_TOKENS, MODEL_ID

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None
_lock = threading.Lock()


def _compute_dtype():
    """bf16 on GPUs with native support (Ampere or newer, e.g. A100/L4), fp16 otherwise (e.g. T4).

    torch.cuda.is_bf16_supported() is also True on a T4, where bf16 is emulated and slow.
    """
    if torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8:
        return torch.bfloat16
    return torch.float16


def load_model():
    """Loads the model and tokenizer once, thread-safely."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    with _lock:
        if _model is not None:
            return _model, _tokenizer

        dtype = _compute_dtype()
        gpu = torch.cuda.get_device_name() if torch.cuda.is_available() else "no GPU"
        logger.info(f"Loading model: {MODEL_ID} on {gpu} (compute dtype {dtype})")
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
        )
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            quantization_config=quantization,
            device_map="auto",
            dtype=dtype,
        )
        _model.eval()
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        logger.info("Model loaded")
        return _model, _tokenizer


def generate(messages: list, max_new_tokens: int = MAX_NEW_TOKENS) -> Generation:
    """Greedily generates a response for chat messages."""
    model, tokenizer = load_model()

    encoded = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt", tokenize=True,
    )
    input_ids = (encoded["input_ids"] if hasattr(encoded, "input_ids") else encoded).to(model.device)
    prompt_len = input_ids.shape[-1]

    start = time.perf_counter()
    with torch.no_grad():
        output_ids = model.generate(
            input_ids,
            attention_mask=torch.ones_like(input_ids),
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    latency_ms = (time.perf_counter() - start) * 1000

    new_tokens = output_ids[0][prompt_len:]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return Generation(text, int(new_tokens.shape[-1]), latency_ms)


def run_inference(user_query: str, tool_docs: str, history: list | None = None) -> Generation:
    """Generates the next step for a query."""
    return generate(build_messages(tool_docs, user_query, history or []))
