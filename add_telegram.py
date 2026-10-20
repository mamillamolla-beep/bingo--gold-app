from pathlib import Path

p = Path("miniapp/index.html")
s = p.read_text()

old = "</script></body></html>"

if "telegram-web-app.js" in s:
    print("Telegram code already exists.")
elif old not in s:
    print("Ending not found; no changes made.")
else:
    new = """</script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
(async function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (!tg) {
    document.querySelector('.pill').textContent = 'OPEN IN TELEGRAM';
    return;
  }
  tg.ready();
  tg.expand();

  const headers = {'X-Telegram-Init-Data': tg.initData};

  try {
    const results = await Promise.all([
      fetch('/api/me', {headers}),
      fetch('/api/game', {headers})
    ]);
    if (!results[0].ok || !results[1].ok) {
      document.querySelector('.pill').textContent = 'VERIFYING…';
      return;
    }

    const me = await results[0].json();
    const game = await results[1].json();
    document.getElementById('balance').textContent = me.balance;

    const called = Array.isArray(game.called) ? game.called : [];
    document.getElementById('calledCount').textContent =
      called.length + ' / 75';

    const set = new Set(called.map(Number));
    document.querySelectorAll('#numbers button').forEach(button => {
      button.classList.toggle('called', set.has(Number(button.textContent)));
    });

    document.querySelector('.pill').textContent =
      game.active ? 'GAME LIVE' : 'WAITING';
  } catch (e) {
    document.querySelector('.pill').textContent = 'CONNECTION ERROR';
  }
})();
</script></body></html>"""
    p.write_text(s.replace(old, new, 1))
    print("Telegram connection code added.")
