# JavaScript SDK

```js
const { Client } = require('./index');

const client = new Client({
  baseUrl: 'http://localhost:3000',
  apiKey: process.env.API_KEY,
});

async function run() {
  const games = await client.games();
  const cards = await client.cards({ game: 'pokemon', q: 'pika' });
  console.log(games, cards);
}

run();
```
