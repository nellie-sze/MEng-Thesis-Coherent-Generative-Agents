import torch
from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer
import requests
from dotenv import load_dotenv
import os
import time
from typing import List
from config.coherence_config import config as coherence_config
from util.logging import log_error, log_info

load_dotenv()
login_token = os.getenv("HF_API_TOKEN") # generate on hugging face
LOCAL = False
HF_API_URL = "https://router.huggingface.co/v1/chat/completions"
DEFAULT_MODEL_ID = coherence_config["model"]


def _build_messages(prompt: str, system_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def _log_progress(done: int, total: int) -> None:
    if total and (done % 100 == 0 or done == total):
        log_info(f"Generated response for prompt number {done}/{total}")


def _generation_kwargs(model_id: str, max_new_tokens: int) -> dict:
    kwargs = {
        "max_new_tokens": max_new_tokens,
        "do_sample": True,
    }
    if "qwen3" in model_id.lower():
        # Recommended by the Qwen3.5 model card for instruct/non-thinking mode.
        kwargs.update({
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "repetition_penalty": 1.0,
        })
    else:
        kwargs.update({
            "temperature": 0.6,
            "top_p": 0.9,
        })
    return kwargs

class LocalHuggingfaceChatAPI:
    def __init__(self, model_id=DEFAULT_MODEL_ID, n_predict=700, gpu_id=0):
        self.model_id = model_id
        self.n_predict = n_predict
        self.gpu_id = gpu_id

        self.device = self.device_name(gpu_id)

        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.pad_token_id = self.tokenizer.pad_token_id
        if self.pad_token_id is None:
            self.pad_token_id = self.tokenizer.eos_token_id
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            cache_dir="./models",
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            device_map={"": self.device},
            token=login_token
        )

        self.use_chat_template = hasattr(self.tokenizer, "apply_chat_template")
        if not self.use_chat_template:
            self.pipe = pipeline(
                "text-generation",
                model=self.model,
                tokenizer=self.tokenizer,
                device=self.gpu_id if torch.cuda.is_available() else -1,
            )
            self.terminators = [
                self.tokenizer.eos_token_id,
                self.tokenizer.convert_tokens_to_ids("<|eot_id|>")
            ]

        self.system_prompt = (
            "You are a highly specialized sociologist and economist with extensive, "
            "evidence-based knowledge of German cultural, behavioral, and economic patterns. "
            "When given descriptions of specific individuals living in Germany, you must provide detailed, "
            "realistic, and unbiased sociological and economic insights that accurately reflect "
            "contemporary social and economic dynamics. Ensure every output is strictly valid JSON."
        )

    def device_name(self, gpu_id):
        if torch.cuda.is_available():
            return f"cuda:{gpu_id}"
        else:
            return "cpu"

    def get_completion(self, prompt):
        return self._generate_response(_build_messages(prompt, self.system_prompt))

    def get_completions(self, prompts):
        responses = []
        for prompt in prompts:
            responses.append(self._generate_response(_build_messages(prompt, self.system_prompt)))
            _log_progress(len(responses), len(prompts))
        return responses

    def _generate_response(self, messages):
        if self.use_chat_template:
            template_kwargs = {
                "add_generation_prompt": True,
                "tokenize": True,
                "return_dict": True,
                "return_tensors": "pt",
                "enable_thinking": False,
            }

            model_inputs = self.tokenizer.apply_chat_template(
                messages,
                **template_kwargs,
            )
            model_inputs = {k: v.to(self.device) for k, v in model_inputs.items()}
            generated_ids = self.model.generate(
                **model_inputs,
                pad_token_id=self.pad_token_id,
                **_generation_kwargs(self.model_id, self.n_predict),
            )
            input_length = model_inputs["input_ids"].shape[1]
            trimmed_ids = generated_ids[:, input_length:]
            response = self.tokenizer.batch_decode(trimmed_ids, skip_special_tokens=True)[0]
            return response
        else:
            outputs = self.pipe(
                messages,
                eos_token_id=self.terminators,
                pad_token_id=self.pad_token_id,
                **_generation_kwargs(self.model_id, self.n_predict),
            )
            full_text = outputs[0]["generated_text"][-1]["content"]
            return full_text
        
class RemoteHuggingfaceChatAPI:
    def __init__(self, model_id=DEFAULT_MODEL_ID, n_predict=3000, gpu_id=0):
        self.model_id = model_id
        self.n_predict = n_predict

        self.headers = {
            "Authorization": f"Bearer {login_token}",
            "Content-Type": "application/json"
        }

        self.system_prompt = (
            "You are a highly specialized sociologist and economist with extensive, "
            "evidence-based knowledge of German cultural, behavioral, and economic patterns. "
            "When given descriptions of specific individuals living in Germany, you must provide detailed, "
            "realistic, and unbiased sociological and economic insights that accurately reflect "
            "contemporary social and economic dynamics. Ensure every output is strictly valid JSON."
        )

    def _call_api(self, prompt: str) -> str:
        temperature = 0.7
        top_p = 0.8
        presence_penalty = 1.5
        if "qwen3.5" in self.model_id.lower():
            temperature = 0.3
            presence_penalty = 0.0

        payload = {
            "model": self.model_id,
            "messages": _build_messages(prompt, self.system_prompt),
            "max_tokens": 5000,
            "temperature": temperature,
            "top_p": top_p,
            "presence_penalty": presence_penalty,
            #"reasoning_effort": "none",
            #"response_format": {"type": "json_object"},
        }
        max_retries = 5
        backoff = 2  # seconds

        for attempt in range(max_retries):
            response = requests.post(
                HF_API_URL,
                headers=self.headers,
                json=payload,
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                message = result["choices"][0]["message"]
                content = message.get("content", "")
                reasoning = message.get("reasoning", "")
                if not content and reasoning:
                    raise RuntimeError(
                        f"Hugging Face backend ignored non-thinking mode for model {self.model_id}: "
                        "received reasoning output with empty content."
                    )
                return content

            if response.status_code == 503:
                wait_time = backoff * (2 ** attempt)
                log_info(f"503 received from Hugging Face. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
                continue

            raise RuntimeError(
                f"Hugging Face API error {response.status_code}: {response.text}"
            )

        raise RuntimeError("Hugging Face API failed after max retries.")

    def get_completion(self, prompt: str) -> str:
        try:
            response = self._call_api(prompt)
            return response
        except Exception as e:
            log_error(e)
            return ""

    def get_completions(self, prompts: List[str]) -> List[str]:
        responses = []
        for prompt in prompts:
            responses.append(self._call_api(prompt))
            _log_progress(len(responses), len(prompts))
        return responses

class HuggingfaceChatAPI:
    def __init__(self, model_id: str = DEFAULT_MODEL_ID, n_predict: int = 700, gpu_id: int = 0):
        if LOCAL:
            self.impl = LocalHuggingfaceChatAPI(model_id=model_id, n_predict=n_predict, gpu_id=gpu_id)
        else:
            self.impl = RemoteHuggingfaceChatAPI(model_id=model_id, n_predict=n_predict, gpu_id=gpu_id)

    def get_completion(self, prompt: str) -> str:
        return self.impl.get_completion(prompt)

    def get_completions(self, prompts: List[str]) -> List[str]:
        return self.impl.get_completions(prompts)

if __name__ == "__main__":
    chat = HuggingfaceChatAPI()
