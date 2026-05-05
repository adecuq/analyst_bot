# analyst_bot
To build and deploy a financial trend-detection bot

# Easy Customizations
- Add more tickers — just edit the WATCHLIST array in analyst_bot.py
- Change delivery time — edit the cron in the .yml: "30 5 * * 1-5" = 5:30 UTC = 7:30 Paris
- Add a Slack notification — replace send_email() with a Slack webhook call (easy ~5 lines)
- Add weekend crypto — change * * 1-5 to * * * and add tickers like BTC-USD, ETH-USD

# Test it locally
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python3 analyst_bot.py
```

# Cost estimation
TokensPrompt (données + mémoire)~3000Réponse (max_tokens)~2200Total~5200 tokens
Prix claude-sonnet-4-6 :

Input : $3 / 1M tokens
Output : $15 / 1M tokens

Par run :

Input : 3000 × $3/1M = $0.009
Output : 2200 × $15/1M = $0.033
Total : ~$0.04 par run

Par mois (1x/semaine) :

4 runs × $0.04 = ~$0.16/mois

Soit moins de $2/an. Le passage de 1800 à 2200 max_tokens ajoute littéralement $0.006 par run — totalement négligeable.