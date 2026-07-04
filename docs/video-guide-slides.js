const { chromium } = require('playwright');
const fs = require('fs');
const SS = process.env.SS, DIR = SS + '/shots';
const shot = f => 'data:image/png;base64,' + fs.readFileSync(`${DIR}/${f}`).toString('base64');

const slides = [
  { type:'title' },
  { n:1, sh:'01_login.png', hT:'התחברות', eT:'Sign in',
    he:'היכנסו עם שם משתמש וסיסמה. חדשים? הזינו קוד הזמנה ואימייל.',
    en:'Log in — or enter your invite code and email to create an account.' },
  { n:2, sh:'02_topbar.png', hT:'חיבור Metricool', eT:'Connect Metricool',
    he:'הקישו על התווית למעלה כדי לחבר את חשבון Metricool. פעם אחת.',
    en:'Tap the chip at the top to link your Metricool account. One time.' },
  { n:3, sh:'03_upload.png', hT:'העלאת סרטון', eT:'Upload your video',
    he:'בלשונית "יצירה", בחרו סרטון (עד 500MB). אנכי מתאים לרילים.',
    en:'In Create, pick a video (up to 500 MB). Vertical works best for reels.' },
  { n:4, sh:'04_options.png', hT:'אפשרויות ועיבוד', eT:'Options & process',
    he:'בחרו: חיתוך שתיקות, כתוביות, שיפור. הקישו הפעלה ותקבלו סרטון חתוך + כתוביות.',
    en:'Pick cut-silences, captions, enhance — then Run for an auto-cut, captioned video.' },
  { n:5, sh:'05_schedule.png', hT:'צריבה ותזמון', eT:'Burn & schedule',
    he:'צרבו את הסרטון הסופי, ותזמנו אותו ל-Metricool עם כיתוב ושעה.',
    en:'Burn the final video, then schedule it to Metricool with a caption and time.' },
  { n:6, sh:'06_history.png', hT:'היסטוריה', eT:'History',
    he:'כל הסרטונים נשמרים ל-30 יום — להורדה, תזמון או מחיקה.',
    en:'Every video is kept for 30 days — re-download, schedule, or delete.' },
  { type:'end' },
];

function frameHTML(s){
  const base = `<style>
    *{margin:0;padding:0;box-sizing:border-box}
    html{-webkit-print-color-adjust:exact}
    body{width:1280px;height:720px;overflow:hidden;font-family:"Heebo","Arial Hebrew",-apple-system,"Segoe UI",Arial,sans-serif;color:#1E1033}
    .wrap{width:1280px;height:720px;display:flex;align-items:center}
    .grad{background:linear-gradient(135deg,#3B0764,#7C3AED);color:#fff;flex-direction:column;justify-content:center;text-align:center}
    .content{background:#F2EEF8;padding:0 70px;gap:60px}
    .phone{width:300px;flex-shrink:0;border-radius:30px;overflow:hidden;border:6px solid #1E1033;box-shadow:0 24px 60px rgba(55,7,100,.35);background:#fff;max-height:600px}
    .phone img{width:100%;display:block}
    .panel{flex:1}
    .num{width:52px;height:52px;border-radius:50%;background:#7C3AED;color:#fff;font-size:24px;font-weight:800;display:flex;align-items:center;justify-content:center;margin-bottom:22px}
    .hT{direction:rtl;text-align:right;font-size:40px;font-weight:800;color:#6D28D9;line-height:1.15}
    .eT{direction:ltr;text-align:left;font-size:23px;font-weight:700;color:#9333EA;margin:4px 0 26px}
    .he{direction:rtl;text-align:right;font-size:24px;line-height:1.5;color:#1E1033;margin-bottom:14px}
    .en{direction:ltr;text-align:left;font-size:19px;line-height:1.5;color:#6B7080}
    .logo{width:96px;height:96px;margin:0 auto 18px}
    .big{font-size:64px;font-weight:800;letter-spacing:1px}
    .sub{font-size:30px;font-weight:600;opacity:.9;margin-top:6px}
    .tag{font-size:22px;opacity:.85;margin-top:22px}
    .badge{margin-top:26px;display:inline-block;background:rgba(255,255,255,.16);border:1.5px solid rgba(255,255,255,.4);border-radius:999px;padding:10px 26px;font-size:22px;font-weight:700}
  </style>`;
  const svg = `<svg class="logo" viewBox="0 0 48 48"><rect x="2" y="2" width="44" height="44" rx="13" fill="rgba(255,255,255,0.16)" stroke="rgba(255,255,255,0.45)" stroke-width="1.5"/><rect x="10" y="17" width="28" height="14" fill="rgba(255,255,255,0.92)"/><rect x="5.5" y="14" width="5" height="20" rx="1.7" fill="#fff"/><rect x="37.5" y="14" width="5" height="20" rx="1.7" fill="#fff"/><polygon points="20.6,19.6 29.6,24.6 20.6,29.6" fill="rgba(59,7,100,0.9)"/></svg>`;
  if (s.type==='title') return base+`<div class="wrap grad">${svg}<div class="big">פייפליין · Pipeline</div><div class="sub">עריכות וידאו בלחיצת כפתור</div><div class="badge">מדריך וידאו · Video Guide</div></div>`;
  if (s.type==='end') return base+`<div class="wrap grad">${svg}<div class="big">מוכנים! · You're set</div><div class="tag">שאלות או משוב? כפתור "צרו קשר" בתחתית האפליקציה<br>Questions or feedback? Use the Contact button in the app</div></div>`;
  return base+`<div class="wrap content">
    <div class="phone"><img src="${shot(s.sh)}"></div>
    <div class="panel"><div class="num">${s.n}</div>
      <div class="hT">${s.hT}</div><div class="eT">${s.eT}</div>
      <div class="he">${s.he}</div><div class="en">${s.en}</div></div>
  </div>`;
}

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport:{width:1280,height:720}, deviceScaleFactor:1 });
  for (let i=0;i<slides.length;i++){
    await p.setContent(frameHTML(slides[i]), { waitUntil:'networkidle' });
    await p.waitForTimeout(150);
    await p.screenshot({ path: `${DIR}/slide_${String(i).padStart(2,'0')}.png` });
  }
  await b.close();
  console.log('rendered', slides.length, 'slides');
})();
