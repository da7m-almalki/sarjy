# Sarjy

Voice assistant that books appointments at a fictional barbershop, built for the Sarj take-home.

You talk to it in the browser, it books a real slot on a public Google Calendar. The deep dive is multistep workflows: keeping the booking state correct when the user changes their mind mid-flow or goes off script.

The plan lives in [docs/PRD.md](docs/PRD.md). Status: the floor works (voice loop, memory, live availability); the booking flow is next.

## Run it

```
cp .env.example .env   # fill in your keys
uv run uvicorn app.main:app --port 8400
```

Open http://localhost:8400 in Chrome or Safari and tap the mic.

