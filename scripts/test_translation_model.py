import torch
from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

MODEL_NAME = "facebook/m2m100_418M"

TESTS = [
    ("hi", "en", "आप कैसे हैं?"),
    ("hi", "en", "आज मौसम बहुत अच्छा है।"),
    ("hi", "en", "मुझे आपसे मिलकर खुशी हुई।"),
    ("bn", "en", "আপনি কেমন আছেন?"),
    ("en", "hi", "How are you today?"),
    ("en", "bn", "How are you today?"),
]

def main():
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable.")

    print("GPU:", torch.cuda.get_device_name(0))
    print("Loading:", MODEL_NAME)

    tokenizer = M2M100Tokenizer.from_pretrained(MODEL_NAME)
    model = M2M100ForConditionalGeneration.from_pretrained(
        MODEL_NAME,
        dtype=torch.float16,
    ).to("cuda")
    model.eval()

    for source_lang, target_lang, text in TESTS:
        tokenizer.src_lang = source_lang

        inputs = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=256,
        ).to("cuda")

        with torch.inference_mode():
            output = model.generate(
                **inputs,
                forced_bos_token_id=tokenizer.get_lang_id(target_lang),
                max_new_tokens=128,
                max_length=None,
            )

        translated = tokenizer.batch_decode(
            output,
            skip_special_tokens=True,
        )[0]

        print(f"\n{source_lang} -> {target_lang}")
        print("Input: ", text)
        print("Output:", translated)

    print(
        "\nPeak GPU memory:",
        round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1),
        "MiB",
    )

if __name__ == "__main__":
    main()
