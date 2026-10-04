"""
Test script for NIGHTFALL Local Brain Engine (v2)
Verifies local LLM server availability, models listing, and test completion.
"""

import sys
from core.local_brain import local_brain

print("=" * 60)
print("  NIGHTFALL EVO v2: LOCAL BRAIN TEST")
print("=" * 60)

available = local_brain.is_available()
print(f"Local LLM Server Status: {'ONLINE' if available else 'OFFLINE'}")
print(f"Endpoint: {local_brain.endpoint}")

if available:
    models = local_brain.list_installed_models()
    print(f"Installed Models: {models if models else 'None (run ollama pull qwen2.5:3b)'}")
    if models:
        test_model = models[0]
        print(f"\nRunning test inference on: {test_model}...")
        try:
            res = local_brain.chat_complete(
                messages=[
                    {"role": "system", "content": "You are NIGHTFALL Evo, a concise desktop AI."},
                    {"role": "user", "content": "Introduce yourself in one punchy sentence."}
                ],
                model=test_model
            )
            reply = res["choices"][0]["message"]["content"]
            print(f"Reply: \"{reply.strip()}\"")
            print("\nLocal Brain is 100% operational!")
        except Exception as e:
            print(f"Inference error: {e}")
else:
    print("\nLocal server not running yet. Once Ollama finishes installing, run:")
    print("  ollama run qwen2.5:3b")
    print("to download the recommended lightweight high-speed model!")
