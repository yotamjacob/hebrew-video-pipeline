// ── פייפליין i18n ──
// English is the source language; Hebrew mirrors it 1:1 with full RTL.
// Static HTML opts in via data-i18n / data-i18n-placeholder / data-i18n-title
// attributes; JS strings go through t('key', {vars}).
(function () {
  'use strict';

  const I18N = {
    // ── Hero / chrome ──
    'hero.sub':        { en: 'Hebrew video editing at the click of a button', he: 'עריכת וידאו בעברית בלחיצת כפתור' },
    'hero.tags':       { en: 'Auto-cut · Captions · Hooks · B-roll · Schedule', he: 'חיתוך אוטומטי · כתוביות · הוקים · בי-רול · תזמון' },
    'upload.bestFor':  { en: 'Built for Hebrew talking videos - the clearer the audio, the better the captions.', he: 'מותאם לסרטוני דיבור בעברית - ככל שהאודיו ברור יותר, הכתוביות טובות יותר.' },
    'footer.contact':  { en: 'Contact us / leave feedback', he: 'צרו קשר / שלחו משוב' },
    'footer.legal':    { en: 'Privacy & Terms', he: 'פרטיות ותנאי שימוש' },
    'boot.waking':     { en: 'One moment, waking up the server...', he: 'רק רגע, מעירים את השרת...' },
    'tab.create':      { en: 'Create', he: 'יצירה' },
    'tab.history':     { en: 'History', he: 'היסטוריה' },
    'tab.guide':       { en: 'Guide', he: 'מדריך' },
    'tab.logout':      { en: 'Sign out', he: 'התנתקות' },
    'reconnect.msg':     { en: 'A previous job is still running - tap to reconnect and get your result.', he: 'עבודה קודמת עדיין רצה - הקישו כדי להתחבר מחדש ולקבל את התוצאה.' },
    'reconnect.resume':  { en: 'Resume', he: 'המשך' },
    'reconnect.dismiss': { en: 'Dismiss', he: 'סגירה' },

    // ── Auth ──
    'auth.title':      { en: 'Sign In', he: 'התחברות' },
    'auth.password':   { en: 'Password', he: 'סיסמה' },
    'auth.invite':     { en: 'Invite code', he: 'קוד הזמנה' },
    'auth.signin':     { en: 'Sign in', he: 'התחברות' },
    'auth.register':   { en: 'Create account', he: 'יצירת חשבון' },
    'auth.toRegister': { en: 'New here? Create an account', he: 'חדשים כאן? צרו חשבון' },
    'auth.toSignin':   { en: 'Already have an account? Sign in', he: 'כבר יש לכם חשבון? התחברו' },
    'auth.working':    { en: '⏳ Working…', he: '⏳ רגע…' },
    'auth.failed':     { en: 'Something went wrong. Try again.', he: 'משהו השתבש. נסו שוב.' },
    'auth.email':      { en: 'Email', he: 'אימייל' },
    'auth.showPw':     { en: 'Show password', he: 'הצג סיסמה' },
    'auth.hidePw':     { en: 'Hide password', he: 'הסתר סיסמה' },
    'auth.remember':   { en: 'Remember me', he: 'זכור אותי' },
    'auth.marketing':  { en: 'I agree to receive updates and promotions from Pipeline', he: 'אני מאשר/ת קבלת עדכונים ופרסומים מפייפליין' },
    'auth.requiredError': { en: 'Please check the required boxes to continue.', he: 'יש לסמן את התיבות הנדרשות כדי להמשיך.' },
    'auth.terms':      { en: 'I have read and agree to the <a href="/legal.html" target="_blank" rel="noopener">Privacy Policy &amp; Terms of Use</a>', he: 'קראתי ואני מסכים/ה ל<a href="/legal.html" target="_blank" rel="noopener">מדיניות הפרטיות ותנאי השימוש</a>' },
    'auth.termsError': { en: 'Please accept the Privacy Policy and Terms of Use to continue.', he: 'יש לאשר את מדיניות הפרטיות ותנאי השימוש כדי להמשיך.' },
    'valid.emailRequired':  { en: 'Please enter your email address.', he: 'יש להזין כתובת אימייל.' },
    'valid.emailInvalid':   { en: 'Please enter a valid email address, like name@example.com.', he: 'יש להזין כתובת אימייל תקינה, למשל name@example.com.' },
    'valid.pwRequired':     { en: 'Please enter a password.', he: 'יש להזין סיסמה.' },
    'valid.pwShort':        { en: 'The password must be at least 8 characters.', he: 'הסיסמה חייבת לכלול לפחות 8 תווים.' },
    'valid.inviteRequired': { en: 'Please enter your invite code.', he: 'יש להזין קוד הזמנה.' },
    'logout.title':    { en: 'Log out?', he: 'להתנתק?' },
    'logout.body':     { en: 'You will need to sign in again next time.', he: 'תצטרכו להתחבר שוב בפעם הבאה.' },
    'logout.confirm':  { en: 'Log out', he: 'התנתקות' },
    'mc.disconnectHint':  { en: 'Connected — tap to disconnect', he: 'מחובר - הקישו לניתוק' },
    'mc.disconnectTitle': { en: 'Disconnect Metricool?', he: 'לנתק את Metricool?' },
    'mc.disconnectBody':  { en: "You won't be able to schedule until you reconnect.", he: 'לא תוכלו לתזמן עד שתתחברו מחדש.' },
    'mc.disconnectOk':    { en: 'Disconnect', he: 'ניתוק' },
    'auth.forgot':     { en: 'Forgot your password?', he: 'שכחתם סיסמה?' },
    'auth.sendReset':  { en: 'Send reset link', he: 'שליחת קישור לאיפוס' },
    'auth.resetSent':  { en: "If that account exists, we've sent a reset link. Check your email.", he: 'אם החשבון קיים, שלחנו קישור לאיפוס. בדקו את האימייל.' },
    'reset.title':     { en: 'Set a new password', he: 'בחירת סיסמה חדשה' },
    'reset.submit':    { en: 'Save new password', he: 'שמירת הסיסמה' },
    'reset.tooShort':  { en: 'Password must be at least 8 characters', he: 'הסיסמה חייבת להיות לפחות 8 תווים' },
    'reset.done':      { en: 'Password updated. You can sign in now.', he: 'הסיסמה עודכנה. אפשר להתחבר עכשיו.' },
    'verify.pending':  { en: 'Please verify your email ({email}) to secure your account.', he: 'אמתו את האימייל ({email}) כדי לאבטח את החשבון.' },
    'verify.noEmail':  { en: 'Add an email so you can recover your account later.', he: 'הוסיפו אימייל כדי שתוכלו לשחזר את החשבון בעתיד.' },
    'verify.resend':   { en: 'Resend', he: 'שליחה מחדש' },
    'verify.add':      { en: 'Save & verify', he: 'שמירה ואימות' },
    'verify.sent':     { en: 'Verification email sent to {email}. Check your inbox.', he: 'נשלח אימייל אימות אל {email}. בדקו את התיבה.' },
    'verify.sendFailed': { en: "Couldn't send right now. Try again in a bit.", he: 'לא הצלחנו לשלוח כרגע. נסו שוב עוד רגע.' },
    'verify.emailPlaceholder': { en: 'you@example.com', he: 'you@example.com' },

    // ── Upload card ──
    'upload.title':    { en: 'Upload Video', he: 'העלאת סרטון' },
    'upload.drop':     { en: 'Drop your video here', he: 'גררו את הסרטון לכאן' },
    'upload.browse':   { en: 'or <span class="upload-link">browse files</span>', he: 'או <span class="upload-link">בחרו קובץ</span>' },
    'upload.hint':     { en: 'MP4 · MOV · MKV &nbsp;·&nbsp; max 20 min &nbsp;·&nbsp; max 500 MB', he: 'MP4 · MOV · MKV &nbsp;·&nbsp; עד 20 דקות &nbsp;·&nbsp; עד 500 MB' },

    // ── Options card ──
    'options.title':       { en: 'Options', he: 'הגדרות' },
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
    'reprocess.btn':       { en: 'Re-process with new settings', he: 'עיבוד מחדש עם הגדרות חדשות' },

    // ── Caption editor ──
    'capedit.title':   { en: 'Edit Captions', he: 'עריכת כתוביות' },
    'capedit.font':    { en: 'Font', he: 'גופן' },
    'capedit.hint':    { en: 'Edit captions below, then burn.', he: 'ערכו את הכתוביות למטה, ואז צרבו.' },
    'capedit.undo':    { en: 'Undo', he: 'ביטול' },
    'capedit.posTitle':  { en: 'Caption vertical position', he: 'מיקום אנכי של הכתובית' },
    'capedit.posTop':    { en: 'Caption near top', he: 'כתובית קרוב לראש' },
    'capedit.posBottom': { en: 'Caption near bottom', he: 'כתובית קרוב לתחתית' },
    'capedit.srt':     { en: 'Download SRT', he: 'הורדת קובץ SRT' },
    'capedit.playpause': { en: 'Play / Pause', he: 'ניגון / השהיה' },
    'capedit.sizeTitle': { en: 'Caption text size in burned video', he: 'גודל הכתוביות בסרטון הסופי' },
    'capedit.badTimes':  { en: 'Fix overlapping caption times before burning.', he: 'תקנו חפיפות בזמני הכתוביות לפני הצריבה.' },

    // ── B-roll cards ──
    'broll.title':        { en: '🎥 B-roll Suggestions', he: '🎥 הצעות בי-רול' },
    'broll.analyzing':    { en: 'Analyzing video for B-roll opportunities…', he: 'מנתח את הסרטון לאיתור הזדמנויות בי-רול…' },
    'stock.title':        { en: 'Stock B-Roll Suggestions', he: 'הצעות בי-רול ממאגרים' },
    'stock.find':         { en: 'Find B-Roll Moments', he: 'איתור רגעים לבי-רול' },
    'stock.rerunMsg':     { en: 'Captions changed since last analysis - re-run to update B-roll suggestions.', he: 'הכתוביות השתנו מאז הניתוח האחרון - הריצו שוב לעדכון ההצעות.' },
    'stock.rerun':        { en: 'Re-run', he: 'הרצה מחדש' },
    'stock.finding':      { en: 'Finding B-roll moments…', he: 'מאתר רגעים לבי-רול…' },

    // ── Hook card ──
    'hook.title':        { en: 'Hook Generator', he: 'מחולל הוקים' },
    'hook.intro':        { en: 'Generate a short opening caption that appears in the first seconds of your video to stop the scroll.', he: 'צרו כיתוב פתיחה קצר שמופיע בשניות הראשונות של הסרטון ועוצר את הגלילה.' },
    'hook.generate':     { en: 'Generate Hook Options', he: 'יצירת אפשרויות הוק' },
    'hook.tipLabel':     { en: 'Hook tip:', he: 'למה זה עובד:' },
    'hook.regenerate':   { en: 'Regenerate hook options', he: 'יצירת אפשרויות הוק מחדש' },
    'hook.regenTitle':   { en: 'Regenerate hooks?', he: 'ליצור הוקים מחדש?' },
    'hook.regenBody':    { en: 'This replaces the current hook options - and any edits you made to them - with a fresh set.', he: 'פעולה זו מחליפה את אפשרויות ההוק הנוכחיות - וכל עריכה שביצעתם בהן - בסט חדש.' },
    'hook.regenOk':      { en: 'Regenerate', he: 'יצירה מחדש' },
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
    'hook.saveLoad':     { en: 'Save / Load Template', he: 'שמירה / טעינה של תבנית' },
    'hook.willBurn':     { en: 'Hook will be included when you burn the video below.', he: 'ההוק ייכלל כשתצרבו את הסרטון למטה.' },

    // ── Run / burn / result ──
    'run.pipeline':     { en: 'Run Pipeline', he: 'הפעלת העיבוד' },
    'run.burn':         { en: '🔥&nbsp; Burn &amp; Download', he: '🔥&nbsp; צריבה והורדה' },
    'burn.success':     { en: 'Done! Your video is ready.', he: 'סיימנו! הסרטון שלכם מוכן.' },
    'burn.downloadAgain': { en: 'Download again', he: 'הורדה חוזרת' },

    // ── Schedule card ──
    'sched.title':       { en: 'Schedule Video', he: 'תזמון סרטון' },
    'sched.discardTitle':{ en: 'Discard this post?', he: 'לבטל את הפוסט?' },
    'sched.discardBody': { en: 'You have unsaved changes to this scheduled post. Close without scheduling?', he: 'יש שינויים שלא נשמרו בפוסט המתוזמן. לסגור בלי לתזמן?' },
    'sched.discardOk':   { en: 'Discard', he: 'סגירה ומחיקה' },
    'sched.open':        { en: 'Schedule this video', he: 'תזמון הסרטון' },
    'mc.connect':        { en: 'Connect Metricool', he: 'חיבור Metricool' },
    'mc.connected':      { en: '✓ Metricool', he: '✓ Metricool' },
    'sched.intro':       { en: 'Schedule this video straight to Metricool. Unless <b>Auto-publish</b> is on, you approve the final publish inside Metricool.', he: 'תזמנו את הסרטון ישירות ל-Metricool. אלא אם <b>פרסום אוטומטי</b> מופעל, הפרסום הסופי מאושר על ידכם בתוך Metricool.' },
    'sched.platforms':   { en: 'Platforms', he: 'פלטפורמות' },
    'sched.publishTime': { en: 'Publish time', he: 'מועד פרסום' },
    'sched.timeHint':    { en: 'Israel time · best slot ~20:00', he: 'שעון ישראל · הזמן המומלץ ~20:00' },
    'sched.autoPublish': { en: 'Auto-publish', he: 'פרסום אוטומטי' },
    'sched.apOff':       { en: 'Off - post waits in Metricool for your approval', he: 'כבוי - הפוסט ממתין לאישורכם ב-Metricool' },
    'sched.apOn':        { en: 'On - Metricool posts it automatically at the scheduled time', he: 'מופעל - Metricool מפרסם אוטומטית במועד שנקבע' },
    'sched.caption':     { en: 'Caption', he: 'כיתוב' },
    'sched.captionPh':   { en: 'Write a caption, or generate a suggestion from the video…', he: 'כתבו כיתוב, או צרו הצעה מתוך הסרטון…' },
    'sched.suggest':     { en: 'Suggest caption from video', he: 'הצעת כיתוב מהסרטון' },
    'sched.suggesting':  { en: '⏳ Generating caption…', he: '⏳ יוצר כיתוב…' },
    'sched.ytDetails':   { en: 'YouTube details', he: 'פרטי YouTube' },
    'sched.required':    { en: 'required', he: 'חובה' },
    'sched.ytTitlePh':   { en: 'Video title (required for YouTube)', he: 'כותרת הסרטון (חובה ל-YouTube)' },
    'sched.public':      { en: 'Public', he: 'ציבורי' },
    'sched.unlisted':    { en: 'Unlisted', he: 'לא רשום' },
    'sched.private':     { en: 'Private', he: 'פרטי' },
    'sched.kids':        { en: 'Made for kids', he: 'מיועד לילדים' },
    'sched.connectNote': { en: 'Connect your Metricool account once to schedule straight from here.', he: 'חברו את חשבון Metricool פעם אחת כדי לתזמן ישירות מכאן.' },
    'sched.connect':     { en: 'Connect Metricool', he: 'חיבור Metricool' },
    'sched.checkAgain':  { en: "I've connected - check again", he: 'התחברתי - בדקו שוב' },
    'sched.schedule':    { en: 'Schedule on Metricool', he: 'תזמון ב-Metricool' },
    'sched.scheduling':  { en: '⏳ Scheduling…', he: '⏳ מתזמן…' },
    'sched.scheduled':   { en: '✅ Scheduled', he: '✅ תוזמן' },
    'sched.sending':     { en: 'Sending to Metricool…', he: 'שולח ל-Metricool…' },
    'sched.okLine':      { en: '✅ Scheduled on Metricool for {date} {time} (Israel time).', he: '✅ תוזמן ב-Metricool ל-{date} {time} (שעון ישראל).' },
    'sched.openLink':    { en: 'Open in Metricool ↗', he: 'פתיחה ב-Metricool ↗' },
    'sched.approveNote': { en: ' Approve the final publish in Metricool.', he: ' אשרו את הפרסום הסופי ב-Metricool.' },
    'sched.autoNote':    { en: ' It will publish automatically at that time.', he: ' הוא יפורסם אוטומטית במועד שנקבע.' },
    'sched.notConnected': { en: "Metricool isn't connected. Tap Connect Metricool first.", he: 'Metricool לא מחובר. הקישו קודם על חיבור Metricool.' },
    'sched.noBrand': { en: "We couldn't find a brand on your Metricool account. Add a brand in Metricool, then try again.", he: 'לא מצאנו מותג בחשבון ה-Metricool שלכם. הוסיפו מותג ב-Metricool ונסו שוב.' },
    'sched.cantSchedule': { en: "Couldn't schedule: {msg}", he: 'התזמון נכשל: {msg}' },
    'sched.fix.platform': { en: 'pick at least one platform', he: 'בחרו לפחות פלטפורמה אחת' },
    'sched.fix.caption':  { en: 'add a caption', he: 'הוסיפו כיתוב' },
    'sched.fix.datetime': { en: 'set a publish date and time', he: 'קבעו תאריך ושעת פרסום' },
    'sched.fix.ytTitle':  { en: 'add a YouTube title', he: 'הוסיפו כותרת ל-YouTube' },
    'sched.fix.burn':     { en: 'burn a video first', he: 'צרבו קודם סרטון' },
    'sched.fixPrefix':    { en: 'Please {list}.', he: 'בבקשה {list}.' },

    // ── Progress / checklist ──
    'prog.title':       { en: 'Progress', he: 'התקדמות' },
    'prog.finalize':    { en: 'Preparing download', he: 'מכין את ההורדה' },
    'prog.upload':      { en: 'Upload video', he: 'העלאת הסרטון' },
    'prog.enhance':     { en: 'Enhance audio', he: 'שיפור אודיו' },
    'prog.cut':         { en: 'Cut silences', he: 'חיתוך שתיקות' },
    'prog.upscale':     { en: 'AI upscale', he: 'שדרוג AI' },
    'prog.burn':        { en: 'Burn captions', he: 'צריבת כתוביות' },
    'prog.keepOpen':    { en: 'Keep this page open while processing', he: 'השאירו את העמוד פתוח בזמן העיבוד' },
    'prog.done':        { en: 'Done! Your video is ready.', he: 'סיימנו! הסרטון שלכם מוכן.' },
    'prog.download':    { en: 'Download video', he: 'הורדת הסרטון' },
    'prog.error':       { en: 'Something went wrong.', he: 'משהו השתבש.' },
    'prog.retry':       { en: 'Try again', he: 'ניסיון חוזר' },
    'startOver':        { en: '↩&nbsp; Start over', he: '↩&nbsp; התחלה מחדש' },

    // ── History ──
    'hist.title':      { en: 'Previous Videos', he: 'סרטונים קודמים' },
    'hist.note':       { en: 'Videos are kept here for 30 days, then deleted automatically. Download anything you want to keep - we do not store backups.', he: 'סרטונים נשמרים כאן ל-30 יום ואז נמחקים אוטומטית. הורידו כל מה שחשוב לכם לשמור - איננו שומרים גיבוי.' },
    'hist.loading':    { en: 'Loading…', he: 'טוען…' },
    'hist.empty':      { en: 'No videos yet - burn your first video and it will show up here.', he: 'אין עדיין סרטונים - צרבו את הסרטון הראשון והוא יופיע כאן.' },
    'hist.download':   { en: 'Download', he: 'הורדה' },
    'hist.schedule':   { en: 'Schedule', he: 'תזמון' },
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
    'file.res4kTitle':    { en: '4K video', he: 'סרטון 4K' },
    'file.res4k':         { en: 'This is a 4K video, so the file is large - the upload may take a few minutes on a mobile connection. Video quality is kept as-is. Keep the app open until the upload finishes.', he: 'זהו סרטון 4K, ולכן הקובץ גדול - ההעלאה עשויה לקחת כמה דקות בחיבור סלולרי. איכות הווידאו נשמרת כמו שהיא. השאירו את האפליקציה פתוחה עד שההעלאה תסתיים.' },
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
    'err.netDropped':    { en: 'The connection dropped {stage}. If processing already started, it is still running on the server - reopen the app and tap Resume.', he: 'החיבור נקטע {stage}. אם העיבוד כבר התחיל, הוא ממשיך לרוץ בשרת - פתחו שוב את האפליקציה והקישו \u05e2\u05dc \"המשך\".' },
    'err.stage.upload':     { en: 'while uploading', he: 'בזמן ההעלאה' },
    'err.stage.spawn':      { en: 'while starting the job', he: 'בזמן הפעלת העבודה' },
    'err.stage.processing': { en: 'while waiting for processing', he: 'בזמן העיבוד' },
    'err.stage.download':   { en: 'while downloading the result', he: 'בזמן הורדת התוצאה' },
    'err.fileUnreadable': { en: 'Could not read the video file from your device - it may have moved or be cloud-synced (e.g. Google Photos). Please select the file again.', he: 'לא ניתן לקרוא את קובץ הווידאו מהמכשיר - ייתכן שהוא הוזז או מסונכרן בענן (למשל Google Photos). בחרו את הקובץ מחדש.' },
    'err.noAudio': { en: 'This video has no audio track. The editor works from the speech in the video (transcription, silence trimming, captions), so it needs a video with sound. Please upload a video that includes audio.', he: 'לסרטון הזה אין פס קול. העורך עובד לפי הדיבור שבסרטון (תמלול, חיתוך שתיקות וכתוביות), ולכן צריך סרטון עם קול. העלו סרטון שכולל אודיו.' },
    'file.cloudTitle': { en: "Can't read this file", he: 'לא ניתן לקרוא את הקובץ' },
    'file.cloud': { en: "This video looks like it's stored in the cloud (e.g. Google Photos) and isn't downloaded to this device, so it can't be uploaded. Open it in your gallery, download it to the device (usually the menu → Download / Save), then select it again.", he: 'נראה שהסרטון שמור בענן (למשל Google Photos) ואינו מורד למכשיר, ולכן לא ניתן להעלות אותו. פתחו אותו בגלריה, הורידו אותו למכשיר (בדרך כלל דרך התפריט ← הורדה / שמירה), ואז בחרו אותו שוב.' },
    'upload.keepOpen':   { en: 'Keep this window open while uploading. Once processing starts, you can switch away - it finishes on our servers.', he: 'השאירו את החלון פתוח בזמן ההעלאה. ברגע שהעיבוד מתחיל אפשר לעבור לאפליקציה אחרת - הוא ממשיך בשרתים שלנו.' },
    'upload.retrying':   { en: 'connection issue - retrying...', he: 'בעיית חיבור - מנסים שוב...' },
    'prog.errLog':       { en: 'Error log', he: 'יומן שגיאות' },
    'err.stage.burn':       { en: 'while burning the video', he: 'בזמן צריבת הסרטון' },

    // ── Net notices (dynamic) ──
    'net.cellTitle': { en: 'Mobile data detected', he: 'זוהתה גלישה סלולרית' },
    'net.slowTitle': { en: 'Slow connection detected', he: 'זוהה חיבור איטי' },
    'net.cellBody':  { en: 'Uploading a large video on cellular may use significant data and take longer than expected. Switch to WiFi if possible.', he: 'העלאת סרטון גדול ברשת סלולרית עלולה לצרוך הרבה נתונים ולקחת יותר זמן. עברו ל-WiFi אם אפשר.' },
    'net.slowBody':  { en: 'Your connection looks slow ({eff}). The upload may take a while - stay on this page.', he: 'החיבור שלכם נראה איטי ({eff}). ההעלאה עשויה לקחת זמן - הישארו בעמוד.' },

    // ── Run / burn buttons (dynamic) ──
    'run.pipelinePlain': { en: '▶  Run Pipeline', he: '▶  הפעלת העיבוד' },
    'run.burnPlain':     { en: '▶  Burn & Download', he: '▶  צריבה והורדה' },
    'run.burnBrolls':    { en: '▶  Burn & Download  (+{n} B-roll{s})', he: '▶  צריבה והורדה  (+{n} בי-רול)' },
    'run.reburnPlain':   { en: '↻  Re-burn & Download', he: '↻  צריבה מחדש והורדה' },
    'run.reburnBrolls':  { en: '↻  Re-burn & Download  (+{n} B-roll{s})', he: '↻  צריבה מחדש והורדה  (+{n} בי-רול)' },
    'run.burning':       { en: '⏳ Burning…', he: '⏳ צורב…' },
    'run.downloading':   { en: '⏳ Downloading…', he: '⏳ מוריד…' },
    'prog.enhanceVideo': { en: 'Enhance video', he: 'שיפור וידאו' },
    'est.simple':        { en: 'Processing: ~{lo}-{hi} min (after upload)', he: 'עיבוד: ~{lo}-{hi} דקות (אחרי ההעלאה)' },

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
    'stock.findDifferent': { en: 'Find different clips', he: 'חיפוש קליפים אחרים' },
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
    'sched.suggestOff':    { en: 'Suggest caption (turn captions on to enable)', he: 'הצעת כיתוב (הפעילו כתוביות כדי לאפשר)' },
    'sched.generating':    { en: 'Generating…', he: 'יוצר…' },
    'sched.captionFailed': { en: "Couldn't generate a caption ({msg}). You can write one manually.", he: 'יצירת הכיתוב נכשלה ({msg}). אפשר לכתוב אחד ידנית.' },

    // ── Quota / admin ──
    'quota.pill':       { en: '{left} of {limit} trial videos left', he: 'נשארו {left} מתוך {limit} סרטוני ניסיון' },
    'quota.pillZero':   { en: 'Trial videos used up', he: 'מכסת סרטוני הניסיון נוצלה' },
    'quota.exhausted':  { en: 'You have used all your trial videos. Contact the app admin to unlock more.', he: 'ניצלתם את כל סרטוני הניסיון. צרו קשר עם מנהל האפליקציה לפתיחת מכסה נוספת.' },
    'tab.admin':        { en: 'Admin', he: 'ניהול' },

    // ── Guide tab ──
    'guide.title':      { en: 'Quick-start guide', he: 'מדריך מהיר' },
    'guide.intro':      { en: 'Everything you need to turn a Hebrew talking video into a captioned, scheduled reel. Search or tap any section to open it.', he: 'כל מה שצריך כדי להפוך סרטון דיבור בעברית לריל עם כתוביות ומתוזמן. חפשו או הקישו על סעיף כדי לפתוח אותו.' },
    'guide.back':       { en: 'Back to where you were', he: 'חזרה למקום שהייתם בו' },
    'guide.infoTip':    { en: 'Open the guide for this', he: 'פתחו את המדריך לחלק הזה' },
    'guide.search':     { en: 'Search the guide...', he: 'חיפוש במדריך...' },
    'guide.noResults':  { en: 'No sections match your search.', he: 'אין סעיפים שתואמים לחיפוש.' },

    'guide.getstarted.title': { en: 'Getting started - account & sign in', he: 'התחלה - חשבון והתחברות' },
    'guide.getstarted.body':  { en: 'Log in with your email and password. New here? You need an invite code to create an account. Every account comes with a few free video credits (see Account & credits below).<ul><li><b>Forgot your password?</b> Use the link on the sign-in screen to get a reset email.</li><li><b>Verify your email</b> so you can recover your account later - tap the banner if you see it.</li></ul>', he: 'היכנסו עם אימייל וסיסמה. חדשים כאן? צריך קוד הזמנה כדי ליצור חשבון. לכל חשבון יש כמה קרדיטים חינמיים לסרטונים (ראו "חשבון וקרדיטים" למטה).<ul><li><b>שכחתם סיסמה?</b> השתמשו בקישור במסך ההתחברות כדי לקבל מייל לאיפוס.</li><li><b>אמתו את האימייל</b> כדי שתוכלו לשחזר את החשבון בעתיד - הקישו על הבאנר אם הוא מופיע.</li></ul>' },
    'guide.upload.title': { en: 'Uploading a video', he: 'העלאת סרטון' },
    'guide.upload.body':  { en: 'In the Create tab, tap Choose File and pick a Hebrew talking video (up to 500 MB).<ul><li>Clearer audio means more accurate captions.</li><li>Vertical videos work best for reels and stories.</li><li>Keep the window open while the file uploads. Once processing starts you can switch away - it finishes on our servers.</li><li>On a shaky mobile connection the upload resumes on its own; if it fails, just pick the file again.</li></ul>', he: 'בלשונית "יצירה" הקישו "בחירת קובץ" ובחרו סרטון דיבור בעברית (עד 500MB).<ul><li>אודיו ברור יותר = כתוביות מדויקות יותר.</li><li>סרטונים אנכיים מתאימים במיוחד לרילים וסטוריז.</li><li>השאירו את החלון פתוח בזמן ההעלאה. ברגע שהעיבוד מתחיל אפשר לעבור לאפליקציה אחרת - הוא ממשיך בשרת.</li><li>בחיבור סלולרי חלש ההעלאה מתחדשת לבד; אם היא נכשלת, פשוט בחרו שוב את הקובץ.</li></ul>' },
    'guide.options.title': { en: 'Edit options', he: 'אפשרויות עריכה' },
    'guide.options.body':  { en: 'Before you run, choose what the pipeline should do. Each processed video uses one credit.<ul><li><b>Cut silences</b> - removes the gaps between spoken parts. The aggressiveness slider sets how long a pause must be before it is cut (higher = tighter).</li><li><b>Hebrew captions</b> - word-level captions burned into the video.</li><li><b>Audio enhance</b> - noise reduction with DeepFilterNet.</li><li><b>Video enhance</b> - Off, light filters, or AI upscale up to 4K (adds processing time).</li><li><b>B-roll suggestions</b> - see the B-roll finder below.</li></ul>', he: 'לפני ההפעלה, בחרו מה הפייפליין יעשה. כל סרטון מעובד מנצל קרדיט אחד.<ul><li><b>חיתוך שתיקות</b> - מסיר את ההפסקות בין קטעי הדיבור. סרגל העוצמה קובע כמה ארוכה צריכה להיות הפסקה כדי שתיחתך (גבוה = צפוף יותר).</li><li><b>כתוביות בעברית</b> - כתוביות ברמת מילה, צרובות בסרטון.</li><li><b>שיפור אודיו</b> - הפחתת רעשים עם DeepFilterNet.</li><li><b>שיפור וידאו</b> - כבוי, פילטרים קלים, או שדרוג AI עד 4K (מוסיף זמן עיבוד).</li><li><b>הצעות בי-רול</b> - ראו את מאתר הבי-רול למטה.</li></ul>' },
    'guide.captions.title': { en: 'Caption editor', he: 'עריכת כתוביות' },
    'guide.captions.body':  { en: 'After processing, review and fix the captions before burning.<ul><li>Edit the text of any line directly.</li><li>Adjust each line start and end time, or use the scissors / plus / minus to split, add, or remove lines.</li><li>Change the font, and set caption size and on-screen position from the preview.</li><li>Play the preview to see exactly how it will look.</li><li>Need the raw subtitles? Download an SRT file.</li></ul>', he: 'אחרי העיבוד, עברו על הכתוביות ותקנו אותן לפני הצריבה.<ul><li>ערכו את הטקסט של כל שורה ישירות.</li><li>כווננו את זמן ההתחלה והסיום של כל שורה, או השתמשו במספריים / פלוס / מינוס כדי לפצל, להוסיף או להסיר שורות.</li><li>שנו את הגופן, וקבעו את גודל הכתובית ומיקומה על המסך מתוך התצוגה המקדימה.</li><li>הפעילו את התצוגה המקדימה כדי לראות בדיוק איך זה ייראה.</li><li>צריכים את קובץ הכתוביות הגולמי? הורידו קובץ SRT.</li></ul>' },
    'guide.hook.title': { en: 'Hook generator', he: 'מחולל ההוקים' },
    'guide.hook.body':  { en: 'A hook is a short opening line that appears in the first seconds to stop the scroll.<ul><li>Tap Generate hook options - the AI reads your transcript and suggests a few, each with a short reason.</li><li>Pick one, then customize font, text color, background, size, position and timing in the preview.</li><li>Save it as a template to reuse your style next time.</li><li>The hook is burned in together with the captions.</li></ul>', he: 'הוק הוא משפט פתיחה קצר שמופיע בשניות הראשונות כדי לעצור את הגלילה.<ul><li>הקישו "יצירת אפשרויות הוק" - ה-AI קורא את התמלול ומציע כמה, כל אחת עם נימוק קצר.</li><li>בחרו אחת, ואז התאימו גופן, צבע טקסט, רקע, גודל, מיקום ותזמון בתצוגה המקדימה.</li><li>שמרו כתבנית כדי להשתמש שוב בסגנון שלכם בפעם הבאה.</li><li>ההוק נצרב יחד עם הכתוביות.</li></ul>' },
    'guide.broll.title': { en: 'B-roll finder', he: 'מאתר הבי-רול' },
    'guide.broll.body':  { en: 'B-roll lays relevant stock clips over parts of your video to keep it visually interesting.<ul><li>Tap Find B-roll moments - the app picks moments from your transcript and fetches matching stock clips from Pexels and Pixabay.</li><li>For any moment you can swap in a different clip.</li><li>Selected clips are composited in when you burn.</li><li>This is the most compute-heavy feature, so it is capped per video. If you change your captions afterward, re-run it to refresh.</li></ul>', he: 'בי-רול מניח קטעי סטוק רלוונטיים מעל חלקים בסרטון כדי לשמור על עניין ויזואלי.<ul><li>הקישו "איתור רגעי בי-רול" - האפליקציה בוחרת רגעים מהתמלול ומביאה קטעי סטוק מתאימים מ-Pexels ומ-Pixabay.</li><li>לכל רגע אפשר להחליף לקטע אחר.</li><li>הקטעים שנבחרו משולבים בסרטון בזמן הצריבה.</li><li>זו התכונה הכבדה ביותר בעיבוד, ולכן היא מוגבלת לכל סרטון. אם שיניתם כתוביות אחר כך, הריצו שוב כדי לרענן.</li></ul>' },
    'guide.burn.title': { en: 'Burn &amp; download', he: 'צריבה והורדה' },
    'guide.burn.body':  { en: 'When you are happy with the edit, tap Burn &amp; download. This renders your captions, hook and B-roll into one final MP4.<ul><li>Burning runs on our servers - you can switch away and come back.</li><li>When it is done the video downloads and is saved to your History.</li><li>Want to change something? Edit and burn again.</li></ul>', he: 'כשאתם מרוצים מהעריכה, הקישו "צריבה והורדה". זה מרנדר את הכתוביות, ההוק והבי-רול לקובץ MP4 סופי אחד.<ul><li>הצריבה רצה בשרתים שלנו - אפשר לעבור לאפליקציה אחרת ולחזור.</li><li>בסיום הסרטון יורד ונשמר בהיסטוריה שלכם.</li><li>רוצים לשנות משהו? ערכו וצְרבו שוב.</li></ul>' },
    'guide.schedule.title': { en: 'Scheduling to social media', he: 'תזמון לרשתות חברתיות' },
    'guide.schedule.body':  { en: 'Send a finished video straight to your social channels through Metricool.<ul><li>Tap Schedule - on the video right after burning, or on any row in History.</li><li>Choose the platforms (Instagram, Facebook, TikTok, YouTube), set the date and time, and write a caption.</li><li>With Auto-publish off (default), the post waits for your final approval inside Metricool. Turn it on to publish automatically.</li><li>You need a connected Metricool account first (see below).</li></ul>', he: 'שלחו סרטון מוגמר ישירות לערוצים החברתיים שלכם דרך Metricool.<ul><li>הקישו "תזמון" - על הסרטון מיד אחרי הצריבה, או על כל שורה בהיסטוריה.</li><li>בחרו פלטפורמות (Instagram, Facebook, TikTok, YouTube), קבעו תאריך ושעה, וכתבו כיתוב.</li><li>כש"פרסום אוטומטי" כבוי (ברירת מחדל), הפוסט ממתין לאישורכם הסופי בתוך Metricool. הפעילו אותו כדי לפרסם אוטומטית.</li><li>צריך קודם חשבון Metricool מחובר (ראו למטה).</li></ul>' },
    'guide.history.title': { en: 'History', he: 'היסטוריה' },
    'guide.history.body':  { en: 'The History tab keeps your finished videos.<ul><li>Re-download, schedule, or delete any video.</li><li><b>Videos are kept for 30 days, then deleted automatically. Download anything you want to keep - we do not store backups.</b></li></ul>', he: 'לשונית "היסטוריה" שומרת את הסרטונים המוגמרים שלכם.<ul><li>אפשר להוריד שוב, לתזמן או למחוק כל סרטון.</li><li><b>הסרטונים נשמרים 30 יום ואז נמחקים אוטומטית. הורידו כל מה שחשוב לכם לשמור - איננו שומרים גיבוי.</b></li></ul>' },
    'guide.metricool.title': { en: 'Connecting Metricool', he: 'חיבור Metricool' },
    'guide.metricool.body':  { en: 'Metricool is the service that publishes your scheduled posts. Connecting is a one-time step.<ul><li>Tap the Connect Metricool chip at the top of the app and link your account.</li><li>Once connected, the chip shows your connection - tap it to disconnect.</li><li>Each user connects their own Metricool account.</li></ul>', he: 'Metricool הוא השירות שמפרסם את הפוסטים המתוזמנים שלכם. החיבור הוא פעולה חד-פעמית.<ul><li>הקישו על תווית "חיבור Metricool" בראש האפליקציה וחברו את החשבון.</li><li>אחרי החיבור, התווית מציגה את החיבור - הקישו עליה כדי להתנתק.</li><li>כל משתמש מחבר את חשבון ה-Metricool שלו.</li></ul>' },
    'guide.account.title': { en: 'Account &amp; credits', he: 'חשבון וקרדיטים' },
    'guide.account.body':  { en: '<ul><li>Each account has a limited number of free video credits. One credit is used every time you process a video (the Options step).</li><li>Your remaining credits show in the pill at the top of the app.</li><li>If your email is not verified yet, a banner at the top of the app lets you verify it or update the address.</li><li>Need more credits or help? Use the Contact link in the footer.</li></ul>', he: '<ul><li>לכל חשבון יש מספר מוגבל של קרדיטים חינמיים לסרטונים. קרדיט אחד מנוצל בכל עיבוד סרטון (שלב האפשרויות).</li><li>הקרדיטים שנותרו מוצגים בתווית שבראש האפליקציה.</li><li>אם כתובת האימייל עדיין לא אומתה, באנר בראש העמוד מאפשר לאמת אותה או לעדכן את הכתובת.</li><li>צריכים עוד קרדיטים או עזרה? השתמשו בקישור "צרו קשר" בתחתית.</li></ul>' },
    'admin.title':      { en: 'User Limits', he: 'מכסות משתמשים' },
    'admin.note':       { en: 'How many videos each account can process (minus 1 = unlimited).', he: 'כמה סרטונים כל חשבון יכול לעבד (מינוס 1 = בלי הגבלה).' },
    'admin.loading':    { en: 'Loading…', he: 'טוען…' },
    'admin.loadFailed': { en: 'Could not load users - try again.', he: 'טעינת המשתמשים נכשלה - נסו שוב.' },
    'admin.used':       { en: 'used {used}', he: 'בשימוש: {used}' },
    'admin.unlimited':  { en: 'unlimited', he: 'בלי הגבלה' },
    'admin.save':       { en: 'Save', he: 'שמירה' },
    'admin.saveFailed': { en: 'Failed', he: 'נכשל' },
    'admin.resetPw':    { en: 'Reset password', he: 'איפוס סיסמה' },
    'admin.newPwPlaceholder': { en: 'New password', he: 'סיסמה חדשה' },
    'admin.setPw':      { en: 'Set', he: 'קביעה' },
    'admin.cancel':     { en: 'Cancel', he: 'ביטול' },
    'admin.pwTooShort': { en: 'Min 8 chars', he: 'לפחות 8 תווים' },

    'hero.hello': { en: 'Hello, {name}', he: 'שלום, {name}' },
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
