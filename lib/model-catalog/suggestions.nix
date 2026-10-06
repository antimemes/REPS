# The `types.llm` combobox hints, generated — never edit a model id here by hand.
# Concrete ids come from model_catalog.json (regenerate with `task models:update`)
# — PROVIDER knowledge, wrapper-agnostic. Value strings use ADB's canonical
# provider prefixes; adapters translate them into their framework's names.
# The static tail
# covers pattern-style providers with no enumerable model list. Shared infra: a
# catalog refresh changes manifests but never condition identity.
{ lib }:

let
  catalog = builtins.fromJSON (builtins.readFile ./model_catalog.json);
  order = [ "anthropic" "openai" "google" "groq" "mistral" "grok" "moonshotai" "openrouter" ];
  fromCatalog = lib.concatMap
    (prefix:
      let p = catalog.providers.${prefix}; in
      map
        (m: {
          value = "${prefix}/${m.id}";
          description = "${m.name}"
            + lib.optionalString (m.release_date != "") " (released ${m.release_date})"
            + ". ${p.credential_hint}";
        })
        p.models)
    order;
  patterns = [
    { value = "vllm/"; description = "vllm/<org>/<model>: a vLLM-served upstream repo id, e.g. vllm/Qwen/Qwen2.5-7B-Instruct. Set VLLM_BASE_URL on the vllm credential profile; API key optional."; }
    { value = "ollama/"; description = "ollama/<tag>: an Ollama library tag, e.g. ollama/qwen2.5:7b-instruct-fp16. Set OLLAMA_BASE_URL to its OpenAI-compatible endpoint on the ollama credential profile; API key optional."; }
    { value = "openrouter/"; description = "openrouter/<org>/<model>: any OpenRouter-hosted model, including ones added since this catalog was generated. Needs OPENROUTER_API_KEY (asked for on first run)."; }
    { value = "azureai/"; description = "Type your Azure deployment name after the slash; endpoint + key asked for on first run."; }
    { value = "openai-api/"; description = "openai-api/<name>/<model>: an OpenAI-compatible server under a named credential set."; }
  ];
in
fromCatalog ++ patterns
