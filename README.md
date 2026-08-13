# im-seo

SEO tooling repo for Internet Moguls hotel/hospitality clients.

## SerpApi

The official [SerpApi](https://serpapi.com/) Node.js client (`serpapi`, from the [awesome-seo-tools](https://github.com/serpapi/awesome-seo-tools) list) is installed for pulling live SERP data (Google search results, etc.) to support SEO research.

Setup:

1. `npm install`
2. Copy `.env.example` to `.env` and set `SERPAPI_KEY` to your SerpApi API key.
3. `npm run example -- "your search query"`

The key is read automatically from `.env` — no need to set it inline or export it yourself.

See `example.js` for basic usage.
