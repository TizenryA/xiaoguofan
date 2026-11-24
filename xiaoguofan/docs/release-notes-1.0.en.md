# 渡鸦の小锅饭 1.0 update notes

<small>duya's xiaoguofan</small>

1.0 is live. Your keys, accounts and activity are all kept, and you don't need to change anything in your chat app.

## What's new

- **Faster and steadier**: all accounts are loaded by one always-on generation service instead of being started separately each time. Replies start sooner and stream in word by word.
- **Still only your own accounts**: every request uses only accounts you imported. If one runs out of quota we switch to another of yours; if all of them are out, you get a clear message with roughly when they'll be back. Other people's accounts are never used.
- **Two routes** (copy them on the Connect page):
  - Optimized route (recommended in China): `https://xiaoguofanapi.tizenty.xyz/v1`, relayed through Los Angeles; usually faster and steadier from mainland China.
  - Original route: `https://aistudio.tizenty.xyz/lite/v1`, connects straight to our server; use this outside China.
  - The same key works on both, so you can switch any time.
- **Easier account import**: paste a cookie string, a Cookie-Editor JSON export, the export script's .json, or cookies.txt, or just drag the file in. We ask Google whose account it is, so there's no email to type.
- **Plan detection**: each account shows its plan, such as Free, Pro or Ultra ("Unknown" until Google tells us).
- **Home now shows how many times you've used each account**: Google doesn't publish quota limits, so we no longer show "remaining". We count successful replies on this site (counts restart daily at 00:00 Beijing time, 16:00 UTC). When Google actually says an account is used up, we mark it and show when it should be back.
- **Clearer Activity page**: success rate, average time to first reply, input and output tokens, and usage by hour or by day. You can filter by result and export a CSV.
- **Anti-cutoff**: turn it on in Connect and the model list gains extra models marked [防截断] (anti-cutoff), which reduce cut-off long replies.

## What you need to do

- Usually nothing.
- If an account in "My accounts" shows an error (for example "Google sign-in has expired" or "The sign-in session doesn't match the Google email you entered"), export it again as the message says and re-import that account.
