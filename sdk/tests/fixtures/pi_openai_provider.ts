export default function fixtureProvider(pi: any) {
  const baseUrl = process.env.M3_PI_FIXTURE_PROVIDER_URL;
  if (!baseUrl) throw new Error("M3_PI_FIXTURE_PROVIDER_URL is required");
  pi.registerProvider("m3-fixture", {
    name: "M3 deterministic provider",
    api: "openai-completions",
    baseUrl,
    apiKey: "m3-fixture-key",
    models: [{
      id: "fixture-model",
      name: "M3 fixture model",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32768,
      maxTokens: 1024,
    }],
  });
}
