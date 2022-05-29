## Au-Tomator Telegram Bot

This is a bot that listens to messages in a telegram channel and posts them to Slack

## Running it

Copy `.envrc_template` to `.envrc` and replace the TODOs.

Either use direnv or evaluate the content of `.envrc`:

```bash
eval $(cat .envrc)
```

Next, run the bot:

```bash
python main.py
```

That's all!