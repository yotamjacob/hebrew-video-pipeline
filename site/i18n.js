// ── פייפליין i18n ──
// English is the source language; Hebrew mirrors it 1:1 with full RTL.
// Static HTML opts in via data-i18n / data-i18n-placeholder / data-i18n-title
// attributes; JS strings go through t('key', {vars}).
(function () {
  'use strict';

  const I18N = {
    // ── Hero / chrome ──
    'hero.sub':        { en: 'Video editing at the click of a button', he: 'עריכות וידאו בלחיצת כפתור' },
    'hero.tags':       { en: 'Auto-cut · Captions · Hooks · B-roll · Schedule', he: 'חיתוך אוטומטי · כתוביות · הוקים · בי-רול · תזמון' },
    'footer.line':     { en: 'פייפליין &nbsp;·&nbsp; Video editing at the click of a button', he: 'פייפליין &nbsp;·&nbsp; עריכות וידאו בלחיצת כפתור' },
    'tab.create':      { en: '🎬 Create', he: '🎬 יצירה' },
    'tab.history':     { en: '🗂️ History', he: '🗂️ היסטוריה' },
    'tab.logout':      { en: '🚪 Logout', he: '🚪 התנתקות' },
    'reconnect.msg':     { en: 'A previous job is still running - tap to reconnect and get your result.', he: 'עבודה קודמת עדיין רצה - הקישו כדי להתחבר מחדש ולקבל את התוצאה.' },
    'reconnect.resume':  { en: 'Resume', he: 'המשך' },
    'reconnect.dismiss': { en: 'Dismiss', he: 'סגירה' },

    // ── Auth ──
    'auth.title':      { en: '🔐 Sign In', he: '🔐 התחברות' },
    'auth.username':   { en: 'Username', he: 'שם משתמש' },
    'auth.password':   { en: 'Password', he: 'סיסמה' },
    'auth.invite':     { en: 'Invite code', he: 'קוד הזמנה' },
    'auth.signin':     { en: 'Sign in', he: 'התחברות' },
    'auth.register':   { en: 'Create account', he: 'יצירת חשבון' },
    'auth.toRegister': { en: 'New here? Create an account', he: 'חדשים כאן? צרו חשבון' },
    'auth.toSignin':   { en: 'Already have an account? Sign in', he: 'כבר יש לכם חשבון? התחברו' },
    'auth.working':    { en: '⏳ Working…', he: '⏳ רגע…' },
    'auth.failed':     { en: 'Something went wrong. Try again.', he: 'משהו השתבש. נסו שוב.' },

    // ── Upload card ──
    'upload.title':    { en: '🎬 Upload Video', he: '🎬 העלאת סרטון' },
    'upload.drop':     { en: 'Drop your video here', he: 'גררו את הסרטון לכאן' },
    'upload.browse':   { en: 'or <span class="upload-link">browse files</span>', he: 'או <span class="upload-link">בחרו קובץ</span>' },
    'upload.hint':     { en: 'MP4 · MOV · MKV &nbsp;·&nbsp; max 20 min &nbsp;·&nbsp; max 500 MB', he: 'MP4 · MOV · MKV &nbsp;·&nbsp; עד 20 דקות &nbsp;·&nbsp; עד 500 MB' },

    // ── Options card ──
    'options.title':       { en: '⚙️ Options', he: '⚙️ הגדרות' },
    'opt.cut.label':       { en: 'Cut silences', he: 'חיתוך שתיקות' },
    'opt.cut.desc':        { en: 'Remove pauses between speech', he: 'הסרת הפסקות בין קטעי דיבור' },
    'opt.aggr.label':      { en: 'How aggressively to cut silences', he: 'עוצמת חיתוך השתיקות' },
    'opt.aggr.gentle':     { en: 'Gentle', he: 'עדין' },
    'opt.aggr.aggressive': { en: 'Aggressive', he: 'אגרסיבי' },
    'opt.captions.label':  { en: 'Burn Hebrew captions', he: 'צריבת כתוביות בעברית' },
    'opt.captions.desc':   { en: 'Word-level subtitles baked into video', he: 'כתוביות ברמת מילה, צרובות בסרטון' },
    'opt.audio.label':     { en: 'Enhance audio', he: 'שיפור אודיו' },
    'opt.audio.desc':      { en: 'DeepFilterNet noise reduction', he: 'הפחתת רעשים עם DeepFilterNet' },
    'opt.video.label':     { en: 'Enhance video', he: 'שיפור וידאו' },
    'ev.off':              { en: 'Off', he: 'כבוי' },
    'ev.filters':          { en: 'Light touch', he: 'עדין' },
    'ev.esrgan':           { en: 'AI upscale', he: 'שדרוג AI' },
    'ev.desc.none':        { en: 'Off - video is untouched', he: 'כבוי - הווידאו נשאר כמו שהוא' },
    'ev.desc.filters':     { en: 'Light denoise, sharpen and color lift', he: 'ניקוי רעש עדין, חידוד והרמת צבע' },
    'ev.desc.esrgan':      { en: 'AI upscale to sharp 4K (Real-ESRGAN + smart sharpen) - <span class="ev-warn">adds a few minutes</span>', he: 'שדרוג AI ל-4K חד (Real-ESRGAN + חידוד חכם) - <span class="ev-warn">מוסיף כמה דקות</span>' },
    'opt.broll.label':     { en: 'Suggest B-rolls (Veo)', he: 'הצעות בי-רול (Veo)' },
    'opt.broll.desc':      { en: 'AI generates video B-rolls with Veo - coming soon', he: 'יצירת בי-רול עם Veo - בקרוב' },
    'opt.brollAspect':     { en: 'B-roll aspect ratio', he: 'יחס תצוגה לבי-רול' },
    'ar.landscape':        { en: 'Landscape', he: 'לרוחב' },
    'ar.portrait':         { en: 'Portrait', he: 'לאורך' },
    'ar.square':           { en: 'Square', he: 'ריבוע' },
    'opt.geminiKey':       { en: 'Gemini API key - get one free at ai.google.dev', he: 'מפתח API של Gemini - חינם ב-ai.google.dev' },
    'opt.anthropicKey':    { en: 'Anthropic API key (optional) - for smarter B-roll descriptions', he: 'מפתח API של Anthropic (רשות) - לתיאורי בי-רול חכמים יותר' },
    'reprocess.btn':       { en: '↻&nbsp; Re-process with new settings', he: '↻&nbsp; עיבוד מחדש עם הגדרות חדשות' },

    // ── Caption editor ──
    'capedit.title':   { en: '✏️ Edit Captions', he: '✏️ עריכת כתוביות' },
    'capedit.font':    { en: 'Font', he: 'גופן' },
    'capedit.hint':    { en: 'Edit captions below, then burn.', he: 'ערכו את הכתוביות למטה, ואז צרבו.' },
    'capedit.srt':     { en: '⬇&nbsp; Download SRT', he: '⬇&nbsp; הורדת קובץ SRT' },
    'capedit.playpause': { en: 'Play / Pause', he: 'ניגון / השהיה' },
    'capedit.sizeTitle': { en: 'Caption text size in burned video', he: 'גודל הכתוביות בסרטון הסופי' },
    'capedit.badTimes':  { en: 'Fix overlapping caption times before burning.', he: 'תקנו חפיפות בזמני הכתוביות לפני הצריבה.' },

    // ── B-roll cards ──
    'broll.title':        { en: '🎥 B-roll Suggestions', he: '🎥 הצעות בי-רול' },
    'broll.analyzing':    { en: 'Analyzing video for B-roll opportunities…', he: 'מנתח את הסרטון לאיתור הזדמנויות בי-רול…' },
    'stock.title':        { en: '🎞️ Stock B-Roll Suggestions', he: '🎞️ הצעות בי-רול ממאגרים' },
    'stock.find':         { en: '🎬 Find B-Roll Moments', he: '🎬 איתור רגעים לבי-רול' },
    'stock.rerunMsg':     { en: 'Captions changed since last analysis - re-run to update B-roll suggestions.', he: 'הכתוביות השתנו מאז הניתוח האחרון - הריצו שוב לעדכון ההצעות.' },
    'stock.rerun':        { en: 'Re-run', he: 'הרצה מחדש' },
    'stock.finding':      { en: 'Finding B-roll moments…', he: 'מאתר רגעים לבי-רול…' },

    // ── Hook card ──
    'hook.title':        { en: '🪝 Hook Generator', he: '🪝 מחולל הוקים' },
    'hook.intro':        { en: 'Generate a short opening caption that appears in the first seconds of your video to stop the scroll.', he: 'צרו כיתוב פתיחה קצר שמופיע בשניות הראשונות של הסרטון ועוצר את הגלילה.' },
    'hook.generate':     { en: '✨ Generate Hook Options', he: '✨ יצירת אפשרויות הוק' },
    'hook.generating':   { en: 'Generating…', he: 'יוצר…' },
    'hook.cancel':       { en: 'Cancel', he: 'ביטול' },
    'hook.font':         { en: 'Font', he: 'גופן' },
    'hook.textColor':    { en: 'Text color', he: 'צבע טקסט' },
    'hook.bgColor':      { en: 'Background color', he: 'צבע רקע' },
    'hook.bgOpacity':    { en: 'Background opacity:', he: 'שקיפות רקע:' },
    'hook.borderColor':  { en: 'Border color', he: 'צבע מסגרת' },
    'hook.borderSize':   { en: 'Border size:', he: 'עובי מסגרת:' },
    'hook.textSize':     { en: 'Text size:', he: 'גודל טקסט:' },
    'hook.startSec':     { en: 'Start (seconds)', he: 'התחלה (שניות)' },
    'hook.durationSec':  { en: 'Duration (seconds)', he: 'משך (שניות)' },
    'hook.vpos':         { en: 'Vertical position', he: 'מיקום אנכי' },
    'hook.top':          { en: 'Top', he: 'למעלה' },
    'hook.bottom':       { en: 'Bottom', he: 'למטה' },
    'hook.saveLoad':     { en: '💾 Save / Load Template', he: '💾 שמירה / טעינה של תבנית' },
    'hook.willBurn':     { en: 'Hook will be included when you burn the video below.', he: 'ההוק ייכלל כשתצרבו את הסרטון למטה.' },

    // ── Run / burn / result ──
    'run.pipeline':     { en: '▶&nbsp; Run Pipeline', he: '▶&nbsp; הפעלת העיבוד' },
    'run.burn':         { en: '🔥&nbsp; Burn &amp; Download', he: '🔥&nbsp; צריבה והורדה' },
    'burn.success':     { en: 'Done! Your video is ready.', he: 'סיימנו! הסרטון שלכם מוכן.' },
    'burn.downloadAgain': { en: '⬇&nbsp; Download again', he: '⬇&nbsp; הורדה חוזרת' },

    // ── Schedule card ──
    'sched.title':       { en: '📅 Schedule This Video', he: '📅 תזמון הסרטון' },
    'sched.intro':       { en: 'Schedule this video straight to Metricool. Unless <b>Auto-publish</b> is on, you approve the final publish inside Metricool.', he: 'תזמנו את הסרטון ישירות ל-Metricool. אלא אם <b>פרסום אוטומטי</b> מופעל, הפרסום הסופי מאושר על ידכם בתוך Metricool.' },
    'sched.platforms':   { en: 'Platforms', he: 'פלטפורמות' },
    'sched.publishTime': { en: 'Publish time', he: 'מועד פרסום' },
    'sched.timeHint':    { en: 'Israel time · best slot ~20:00', he: 'שעון ישראל · הזמן המומלץ ~20:00' },
    'sched.autoPublish': { en: 'Auto-publish', he: 'פרסום אוטומטי' },
    'sched.apOff':       { en: 'Off - post waits in Metricool for your approval', he: 'כבוי - הפוסט ממתין לאישורכם ב-Metricool' },
    'sched.apOn':        { en: 'On - Metricool posts it automatically at the scheduled time', he: 'מופעל - Metricool מפרסם אוטומטית במועד שנקבע' },
    'sched.caption':     { en: 'Caption', he: 'כיתוב' },
    'sched.captionPh':   { en: 'Write a caption, or generate a suggestion from the video…', he: 'כתבו כיתוב, או צרו הצעה מתוך הסרטון…' },
    'sched.suggest':     { en: '✨ Suggest caption from video', he: '✨ הצעת כיתוב מהסרטון' },
    'sched.suggesting':  { en: '⏳ Generating caption…', he: '⏳ יוצר כיתוב…' },
    'sched.ytDetails':   { en: 'YouTube details', he: 'פרטי YouTube' },
    'sched.required':    { en: 'required', he: 'חובה' },
    'sched.ytTitlePh':   { en: 'Video title (required for YouTube)', he: 'כותרת הסרטון (חובה ל-YouTube)' },
    'sched.public':      { en: 'Public', he: 'ציבורי' },
    'sched.unlisted':    { en: 'Unlisted', he: 'לא רשום' },
    'sched.private':     { en: 'Private', he: 'פרטי' },
    'sched.kids':        { en: 'Made for kids', he: 'מיועד לילדים' },
    'sched.connectNote': { en: '🔗 Connect your Metricool account once to schedule straight from here.', he: '🔗 חברו את חשבון Metricool פעם אחת כדי לתזמן ישירות מכאן.' },
    'sched.connect':     { en: 'Connect Metricool', he: 'חיבור Metricool' },
    'sched.checkAgain':  { en: "I've connected - check again", he: 'התחברתי - בדקו שוב' },
    'sched.schedule':    { en: '🚀&nbsp; Schedule on Metricool', he: '🚀&nbsp; תזמון ב-Metricool' },
    'sched.scheduling':  { en: '⏳ Scheduling…', he: '⏳ מתזמן…' },
    'sched.scheduled':   { en: '✅ Scheduled', he: '✅ תוזמן' },
    'sched.sending':     { en: 'Sending to Metricool…', he: 'שולח ל-Metricool…' },
    'sched.okLine':      { en: '✅ Scheduled on Metricool for {date} {time} (Israel time).', he: '✅ תוזמן ב-Metricool ל-{date} {time} (שעון ישראל).' },
    'sched.openLink':    { en: 'Open in Metricool ↗', he: 'פתיחה ב-Metricool ↗' },
    'sched.approveNote': { en: ' Approve the final publish in Metricool.', he: ' אשרו את הפרסום הסופי ב-Metricool.' },
    'sched.autoNote':    { en: ' It will publish automatically at that time.', he: ' הוא יפורסם אוטומטית במועד שנקבע.' },
    'sched.notConnected': { en: "Metricool isn't connected. Tap Connect Metricool first.", he: 'Metricool לא מחובר. הקישו קודם על חיבור Metricool.' },
    'sched.cantSchedule': { en: "Couldn't schedule: {msg}", he: 'התזמון נכשל: {msg}' },
    'sched.fix.platform': { en: 'pick at least one platform', he: 'בחרו לפחות פלטפורמה אחת' },
    'sched.fix.caption':  { en: 'add a caption', he: 'הוסיפו כיתוב' },
    'sched.fix.datetime': { en: 'set a publish date and time', he: 'קבעו תאריך ושעת פרסום' },
    'sched.fix.ytTitle':  { en: 'add a YouTube title', he: 'הוסיפו כותרת ל-YouTube' },
    'sched.fix.burn':     { en: 'burn a video first', he: 'צרבו קודם סרטון' },
    'sched.fixPrefix':    { en: 'Please {list}.', he: 'בבקשה {list}.' },

    // ── Progress / checklist ──
    'prog.title':       { en: '⏳ Progress', he: '⏳ התקדמות' },
    'prog.upload':      { en: 'Upload video', he: 'העלאת הסרטון' },
    'prog.enhance':     { en: 'Enhance audio', he: 'שיפור אודיו' },
    'prog.cut':         { en: 'Cut silences', he: 'חיתוך שתיקות' },
    'prog.upscale':     { en: 'AI upscale', he: 'שדרוג AI' },
    'prog.burn':        { en: 'Burn captions', he: 'צריבת כתוביות' },
    'prog.keepOpen':    { en: 'Keep this page open while processing', he: 'השאירו את העמוד פתוח בזמן העיבוד' },
    'prog.done':        { en: 'Done! Your video is ready.', he: 'סיימנו! הסרטון שלכם מוכן.' },
    'prog.totalTime':   { en: 'Total processing time: {time}', he: 'זמן עיבוד כולל: {time}' },
    'prog.saved':       { en: '⚡ Saved you <b>~{min} min</b> of manual editing', he: '⚡ חסכנו לכם <b>כ-{min} דקות</b> של עריכה ידנית' },
    'prog.trimmed':     { en: ' · ✂︎ {dur} of silence removed', he: ' · ✂︎ {dur} של שתיקות הוסרו' },
    'prog.download':    { en: '⬇&nbsp; Download video', he: '⬇&nbsp; הורדת הסרטון' },
    'prog.error':       { en: 'Something went wrong.', he: 'משהו השתבש.' },
    'prog.retry':       { en: 'Try again', he: 'ניסיון חוזר' },
    'startOver':        { en: '↩&nbsp; Start over', he: '↩&nbsp; התחלה מחדש' },

    // ── History ──
    'hist.title':      { en: '🗂️ Previous Videos', he: '🗂️ סרטונים קודמים' },
    'hist.note':       { en: 'Finished videos are kept for 30 days, then deleted automatically.', he: 'סרטונים מוכנים נשמרים 30 יום ואז נמחקים אוטומטית.' },
    'hist.loading':    { en: 'Loading…', he: 'טוען…' },
    'hist.empty':      { en: 'No videos yet - burn your first video and it will show up here.', he: 'אין עדיין סרטונים - צרבו את הסרטון הראשון והוא יופיע כאן.' },
    'hist.download':   { en: 'Download', he: 'הורדה' },
    'hist.delete':     { en: 'Delete', he: 'מחיקה' },
    'hist.deleteTitle': { en: 'Delete this video?', he: 'למחוק את הסרטון?' },
    'hist.deleteBody': { en: '“{name}” will be removed from history and can no longer be downloaded.', he: '"{name}" יוסר מההיסטוריה ולא ניתן יהיה להוריד אותו יותר.' },
    'hist.loadFailed': { en: "Couldn't load history. Pull to refresh or try again later.", he: 'טעינת ההיסטוריה נכשלה. נסו לרענן או חזרו מאוחר יותר.' },

    // ── Confirm dialog / modals ──
    'confirm.burnTitle': { en: 'Ready to burn?', he: 'מוכנים לצרוב?' },
    'confirm.burnBody':  { en: 'Finished editing? Click Burn to generate and download your final video.', he: 'סיימתם לערוך? לחצו על צריבה כדי ליצור ולהוריד את הסרטון הסופי.' },
    'confirm.cancel':    { en: 'Cancel', he: 'ביטול' },
    'confirm.burnOk':    { en: 'Burn &amp; Download', he: 'צריבה והורדה' },
    'confirm.delete':    { en: 'Delete', he: 'מחיקה' },
    'tpl.title':         { en: 'Hook Design Templates', he: 'תבניות עיצוב להוק' },
    'tpl.namePh':        { en: 'Template name…', he: 'שם התבנית…' },
    'tpl.save':          { en: 'Save', he: 'שמירה' },
    'tpl.none':          { en: 'No saved templates yet.', he: 'אין עדיין תבניות שמורות.' },
    'tpl.close':         { en: 'Close', he: 'סגירה' },
    'tpl.load':          { en: 'Load', he: 'טעינה' },

    // ── Notices / validation (app.js) ──
    'notice.tooLongTitle': { en: 'Video too long', he: 'הסרטון ארוך מדי' },
    'notice.tooLong':      { en: 'Max length is 20 minutes. This video is {dur}. Please trim it before uploading.', he: 'האורך המרבי הוא 20 דקות. הסרטון הזה באורך {dur}. קצרו אותו לפני ההעלאה.' },
    'notice.tooBigTitle':  { en: 'File too large', he: 'הקובץ גדול מדי' },
    'notice.tooBig':       { en: 'Max size is 500 MB. This file is {size}. Try a lower resolution or shorter clip.', he: 'הגודל המרבי הוא 500 MB. הקובץ הזה שוקל {size}. נסו רזולוציה נמוכה יותר או קטע קצר יותר.' },
    'notice.longTitle':    { en: 'Long video', he: 'סרטון ארוך' },
    'notice.long':         { en: '{dur} - processing will take a few minutes. Keep the page open.', he: '{dur} - העיבוד ייקח כמה דקות. השאירו את העמוד פתוח.' },
    'notice.netTitle':     { en: 'Mobile data detected', he: 'זוהתה גלישה סלולרית' },
    'notice.net':          { en: 'Uploading {size} may be slow or use your data plan. Wi-Fi is recommended.', he: 'העלאת {size} עלולה להיות איטית ולצרוך מחבילת הגלישה. מומלץ Wi-Fi.' },
    'est.short':           { en: 'Estimated processing time: ~{min} min', he: 'זמן עיבוד משוער: כ-{min} דקות' },
    'est.range':           { en: 'Estimated processing time: {lo}-{hi} min', he: 'זמן עיבוד משוער: {lo}-{hi} דקות' },

    // ── Aggressiveness labels ──
    'aggr.1': { en: 'Gentle - cuts pauses over 1.5 s', he: 'עדין - חותך הפסקות מעל 1.5 שניות' },
    'aggr.2': { en: 'Mild - cuts pauses over 0.8 s', he: 'מתון - חותך הפסקות מעל 0.8 שניות' },
    'aggr.3': { en: 'Balanced - cuts pauses over 0.5 s', he: 'מאוזן - חותך הפסקות מעל 0.5 שניות' },
    'aggr.4': { en: 'Aggressive - cuts pauses over 0.3 s', he: 'אגרסיבי - חותך הפסקות מעל 0.3 שניות' },
    'aggr.5': { en: 'Very aggressive - cuts pauses over 0.2 s', he: 'אגרסיבי מאוד - חותך הפסקות מעל 0.2 שניות' },

    // ── Auth / session (dynamic) ──
    'auth.sessionExpired': { en: 'Session expired - please sign in again.', he: 'החיבור פג - נא להתחבר שוב.' },
    'auth.errStatus':      { en: 'Error {status}', he: 'שגיאה {status}' },

    // ── File validation (dynamic) ──
    'file.badTypeTitle':  { en: 'Unsupported file type', he: 'סוג קובץ לא נתמך' },
    'file.badType':       { en: 'Please upload a video file (MP4, MOV, MKV).', he: 'נא להעלות קובץ וידאו (MP4, MOV, MKV).' },
    'file.tooLargeMeta':  { en: '{size} · too large', he: '{size} · גדול מדי' },
    'file.tooLargeTitle': { en: 'File too large', he: 'הקובץ גדול מדי' },
    'file.tooLarge':      { en: 'Max size is 500 MB. This file is {size}. Please trim or compress it first.', he: 'הגודל המרבי הוא 500 MB. הקובץ הזה שוקל {size}. קצרו או דחסו אותו קודם.' },
    'file.reading':       { en: '{size} · reading…', he: '{size} · קורא…' },
    'file.largeWarnTitle': { en: 'Large file', he: 'קובץ גדול' },
    'file.largeWarn':     { en: '{size} - the upload itself may take 30-60 seconds depending on your connection.', he: '{size} - ההעלאה עצמה עשויה לקחת 30-60 שניות, תלוי בחיבור.' },
    'file.removeConfirm': { en: 'Remove the attached video?', he: 'להסיר את הסרטון שצורף?' },

    // ── Reconnect / errors (dynamic) ──
    'err.cached':        { en: 'cached', he: 'שמור' },
    'err.reconnect':     { en: 'Could not reconnect - job may have expired. Please start again.', he: 'החיבור מחדש נכשל - ייתכן שהעבודה פגה. התחילו מחדש.' },
    'err.spawn':         { en: 'Spawn failed ({status})', he: 'הפעלת העיבוד נכשלה ({status})' },
    'err.chunk':         { en: 'Upload failed at chunk {i} ({status})', he: 'ההעלאה נכשלה במקטע {i} ({status})' },
    'err.chunkRetries':  { en: 'Upload failed at chunk {i} after {n} attempts ({status})', he: 'ההעלאה נכשלה במקטע {i} אחרי {n} ניסיונות ({status})' },
    'err.server':        { en: 'Server error ({status}): {text}', he: 'שגיאת שרת ({status}): {text}' },
    'err.network':       { en: 'Network error - check your connection and try again.', he: 'שגיאת רשת - בדקו את החיבור ונסו שוב.' },
    'err.uploadTimeout': { en: 'Upload timed out.', he: 'זמן ההעלאה פג.' },
    'err.resultTimeout': { en: 'Timed out waiting for server result. The job may have failed - check Modal logs.', he: 'תם הזמן בהמתנה לתוצאה מהשרת. ייתכן שהעבודה נכשלה.' },
    'err.unknown':       { en: 'Unknown error', he: 'שגיאה לא ידועה' },

    // ── Net notices (dynamic) ──
    'net.cellTitle': { en: 'Mobile data detected', he: 'זוהתה גלישה סלולרית' },
    'net.slowTitle': { en: 'Slow connection detected', he: 'זוהה חיבור איטי' },
    'net.cellBody':  { en: 'Uploading a large video on cellular may use significant data and take longer than expected. Switch to WiFi if possible.', he: 'העלאת סרטון גדול ברשת סלולרית עלולה לצרוך הרבה נתונים ולקחת יותר זמן. עברו ל-WiFi אם אפשר.' },
    'net.slowBody':  { en: 'Your connection looks slow ({eff}). The upload may take a while - stay on this page.', he: 'החיבור שלכם נראה איטי ({eff}). ההעלאה עשויה לקחת זמן - הישארו בעמוד.' },

    // ── Run / burn buttons (dynamic) ──
    'run.pipelinePlain': { en: '▶  Run Pipeline', he: '▶  הפעלת העיבוד' },
    'run.burnPlain':     { en: '▶  Burn & Download', he: '▶  צריבה והורדה' },
    'run.burnBrolls':    { en: '▶  Burn & Download  (+{n} B-roll{s})', he: '▶  צריבה והורדה  (+{n} בי-רול)' },
    'run.burning':       { en: '⏳ Burning…', he: '⏳ צורב…' },
    'run.downloading':   { en: '⏳ Downloading…', he: '⏳ מוריד…' },
    'prog.enhanceVideo': { en: 'Enhance video', he: 'שיפור וידאו' },
    'est.simple':        { en: 'Estimated {lo}-{hi} min', he: 'הערכה: {lo}-{hi} דקות' },

    // ── Confirm modals (dynamic) ──
    'confirm.ok':          { en: 'Confirm', he: 'אישור' },
    'confirm.startTitle':  { en: 'Start over?', he: 'להתחיל מחדש?' },
    'confirm.startBody':   { en: 'This will clear the current video and all edits, and stop any job still running on the server. Downloaded files are safe.', he: 'הפעולה תנקה את הסרטון הנוכחי ואת כל העריכות, ותעצור כל עבודה שעדיין רצה בשרת. קבצים שהורדתם בטוחים.' },
    'confirm.startOk':     { en: 'Start over', he: 'התחלה מחדש' },

    // ── Veo B-roll (dynamic) ──
    'veo.later':      { en: 'Veo video generation - maybe later!', he: 'יצירת וידאו עם Veo - אולי בהמשך!' },
    'veo.none':       { en: 'No B-roll suggestions found.', he: 'לא נמצאו הצעות בי-רול.' },
    'veo.timeout':    { en: 'Analysis timed out or Gemini is overloaded - try again.', he: 'הניתוח נכשל או ש-Gemini עמוס - נסו שוב.' },
    'veo.failed':     { en: 'Analysis failed: {msg}', he: 'הניתוח נכשל: {msg}' },
    'veo.retry':      { en: 'Retry', he: 'ניסיון חוזר' },
    'veo.newVideo':   { en: '↻ new video', he: '↻ וידאו חדש' },
    'veo.include':    { en: 'Include this B-roll in the final video', he: 'לכלול את הבי-רול הזה בסרטון הסופי' },
    'veo.unavailable': { en: 'Video generation unavailable.', he: 'יצירת וידאו אינה זמינה.' },
    'veo.soon':       { en: 'Coming soon', he: 'בקרוב' },

    // ── Caption time validation (dynamic titles) ──
    'cap.negStart':   { en: 'Start time cannot be negative', he: 'זמן ההתחלה לא יכול להיות שלילי' },
    'cap.overlapPrev': { en: 'Start overlaps previous caption (ends at {t}s)', he: 'ההתחלה חופפת לכתובית הקודמת (מסתיימת ב-{t} שניות)' },
    'cap.seek':       { en: 'Click to seek player here', he: 'לחצו כדי לדלג לנקודה זו בנגן' },
    'cap.endAfter':   { en: 'End must be after start', he: 'הסיום חייב להיות אחרי ההתחלה' },
    'cap.pastEnd':    { en: 'End exceeds video duration ({t}s)', he: 'הסיום חורג ממשך הסרטון ({t} שניות)' },
    'cap.overlapNext': { en: 'End overlaps next caption (starts at {t}s)', he: 'הסיום חופף לכתובית הבאה (מתחילה ב-{t} שניות)' },
    'cap.split':      { en: 'Split at cursor', he: 'פיצול בנקודת הסמן' },
    'cap.addLine':    { en: 'Add caption line below', he: 'הוספת שורת כתובית מתחת' },
    'cap.removeLine': { en: 'Remove this line', he: 'הסרת השורה' },

    // ── Hook generator (dynamic) ──
    'hook.failed':    { en: 'Failed: {msg}', he: 'נכשל: {msg}' },
    'hook.none':      { en: 'No hooks generated - try again.', he: 'לא נוצרו הוקים - נסו שוב.' },
    'hook.clickEdit': { en: 'Click to edit', he: 'לחצו לעריכה' },
    'tpl.apply':      { en: 'Apply', he: 'החלה' },

    // ── Stock B-roll (dynamic) ──
    'stock.searching':   { en: '⏳ Searching…', he: '⏳ מחפש…' },
    'stock.costLimit':   { en: 'Processed top {p} of {t} moments to stay within budget.', he: 'עובדו {p} הרגעים המובילים מתוך {t} כדי להישאר בתקציב.' },
    'stock.noMoments':   { en: 'No strong moments identified in this video.', he: 'לא זוהו רגעים חזקים בסרטון הזה.' },
    'stock.edgeDrops':   { en: '{n} near video edges', he: '{n} קרובים מדי לקצוות הסרטון' },
    'stock.spacingDrops': { en: '{n} too close together', he: '{n} קרובים מדי זה לזה' },
    'stock.dropped':     { en: "{n} moment{s} identified but didn't meet placement rules{detail}.", he: '{n} רגעים זוהו אך לא עמדו בכללי המיקום{detail}.' },
    'stock.failedRetry': { en: 'Failed: {msg}', he: 'נכשל: {msg}' },
    'stock.momentsFound': { en: '{n} moment{s} found', he: 'נמצאו {n} רגעים' },
    'stock.emphasis':    { en: '{n} emphasis', he: '{n} הדגשה' },
    'stock.coverage':    { en: '{n} rhythm/coverage', he: '{n} קצב/כיסוי' },
    'stock.skipRhythm':  { en: 'Skip this rhythm moment', he: 'דילוג על רגע הקצב הזה' },
    'stock.skip':        { en: 'Skip this moment', he: 'דילוג על הרגע הזה' },
    'stock.restore':     { en: 'Restore', he: 'שחזור' },
    'stock.rhythmTitle': { en: 'Coverage moment - added for visual rhythm, not an emphasis peak', he: 'רגע כיסוי - נוסף לקצב ויזואלי, לא שיא הדגשה' },
    'stock.rhythm':      { en: 'rhythm', he: 'קצב' },
    'stock.intensity':   { en: 'intensity {n}/10', he: 'עוצמה {n}/10' },
    'stock.noMatch':     { en: 'No stock clips matched this moment - try "Find different clips" with a broader query.', he: 'אף קליפ לא התאים לרגע הזה - נסו "חיפוש קליפים אחרים" עם שאילתה רחבה יותר.' },
    'stock.findDifferent': { en: '🔄 Find different clips', he: '🔄 חיפוש קליפים אחרים' },
    'stock.scoring':     { en: 'Scoring clips…', he: 'מדרג קליפים…' },
    'stock.noClips':     { en: 'No clips found for "{q}".', he: 'לא נמצאו קליפים עבור "{q}".' },
    'stock.thisMoment':  { en: 'this moment', he: 'הרגע הזה' },
    'stock.clipAlt':     { en: 'Stock clip', he: 'קליפ מאגר' },
    'stock.by':          { en: 'by {author}', he: 'מאת {author}' },
    'stock.useForVideo': { en: 'Use for video', he: 'שימוש בסרטון' },
    'stock.view':        { en: 'View ↗', he: 'צפייה ↗' },
    'stock.tooShort':    { en: '⚠ clip too short ({s}s)', he: '⚠ הקליפ קצר מדי ({s} שניות)' },
    'stock.useRange':    { en: 'Use {in}s → {out}s', he: 'שימוש {in} עד {out} שניות' },
    'stock.videoErr':    { en: '⚠ {msg}', he: '⚠ {msg}' },

    // ── History (dynamic) ──
    'hist.loadError':   { en: 'Could not load history - try again in a moment.', he: 'טעינת ההיסטוריה נכשלה - נסו שוב עוד רגע.' },
    'hist.videoFallback': { en: 'video', he: 'סרטון' },

    // ── Schedule (dynamic) ──
    'sched.suggestOff':    { en: '✨ Suggest caption (turn captions on to enable)', he: '✨ הצעת כיתוב (הפעילו כתוביות כדי לאפשר)' },
    'sched.generating':    { en: '✨ Generating…', he: '✨ יוצר…' },
    'sched.captionFailed': { en: "Couldn't generate a caption ({msg}). You can write one manually.", he: 'יצירת הכיתוב נכשלה ({msg}). אפשר לכתוב אחד ידנית.' },

    // ── Quota / admin ──
    'quota.pill':       { en: '{left} of {limit} trial videos left', he: 'נשארו {left} מתוך {limit} סרטוני ניסיון' },
    'quota.pillZero':   { en: 'Trial videos used up', he: 'מכסת סרטוני הניסיון נוצלה' },
    'quota.exhausted':  { en: 'You have used all your trial videos. Contact the app admin to unlock more.', he: 'ניצלתם את כל סרטוני הניסיון. צרו קשר עם מנהל האפליקציה לפתיחת מכסה נוספת.' },
    'tab.admin':        { en: '🛠️ Admin', he: '🛠️ ניהול' },
    'admin.title':      { en: '🛠️ User Limits', he: '🛠️ מכסות משתמשים' },
    'admin.note':       { en: 'How many videos each account can process (minus 1 = unlimited).', he: 'כמה סרטונים כל חשבון יכול לעבד (מינוס 1 = בלי הגבלה).' },
    'admin.loading':    { en: 'Loading…', he: 'טוען…' },
    'admin.loadFailed': { en: 'Could not load users - try again.', he: 'טעינת המשתמשים נכשלה - נסו שוב.' },
    'admin.used':       { en: 'used {used}', he: 'בשימוש: {used}' },
    'admin.unlimited':  { en: 'unlimited', he: 'בלי הגבלה' },
    'admin.save':       { en: 'Save', he: 'שמירה' },
    'admin.saveFailed': { en: 'Failed', he: 'נכשל' },

    'hero.hello': { en: 'Hello, {name} 👋', he: 'שלום, {name} 👋' },
    'quota.confirmTitle': { en: 'Use 1 trial video?', he: 'להשתמש בסרטון ניסיון אחד?' },
    'quota.confirmBody':  { en: 'You have {left} of {limit} trial videos left. Processing this video will use one of them.', he: 'נשארו לכם {left} מתוך {limit} סרטוני ניסיון. עיבוד הסרטון ישתמש באחד מהם.' },
    'quota.confirmOk':    { en: 'Process video', he: 'עיבוד הסרטון' },

    // ── Language toggle ──
    'lang.switch': { en: 'עברית', he: 'English' },
  };

  let _lang = localStorage.getItem('hebpipe_lang') || 'he';
  if (_lang !== 'en' && _lang !== 'he') _lang = 'he';

  function t(key, vars) {
    const entry = I18N[key];
    let s = entry ? (entry[_lang] || entry.en) : key;
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  }

  function applyI18n(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(el => {
      const s = t(el.getAttribute('data-i18n'));
      if (s.indexOf('<') !== -1 || s.indexOf('&') !== -1) el.innerHTML = s;
      else el.textContent = s;
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = t(el.getAttribute('data-i18n-title'));
    });
  }

  function setLang(lang) {
    _lang = lang === 'he' ? 'he' : 'en';
    localStorage.setItem('hebpipe_lang', _lang);
    document.documentElement.lang = _lang;
    document.documentElement.dir = _lang === 'he' ? 'rtl' : 'ltr';
    applyI18n();
    document.dispatchEvent(new CustomEvent('langchange', { detail: { lang: _lang } }));
  }

  function toggleLang() { setLang(_lang === 'en' ? 'he' : 'en'); }
  function currentLang() { return _lang; }

  window.t = t;
  window.applyI18n = applyI18n;
  window.setLang = setLang;
  window.toggleLang = toggleLang;
  window.currentLang = currentLang;

  // Apply persisted language as soon as the DOM exists (script sits at end of body).
  document.documentElement.lang = _lang;
  document.documentElement.dir = _lang === 'he' ? 'rtl' : 'ltr';
  if (_lang !== 'en') applyI18n();
})();
