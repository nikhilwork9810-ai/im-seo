const { getJson } = require("serpapi");

if (!process.env.SERPAPI_KEY) {
  throw new Error("SERPAPI_KEY environment variable is not set (see .env.example)");
}

async function main() {
  const query = process.argv[2] || "best hotels in goa";

  const results = await getJson({
    engine: "google",
    q: query,
    api_key: process.env.SERPAPI_KEY,
  });

  console.log(JSON.stringify(results.organic_results, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
