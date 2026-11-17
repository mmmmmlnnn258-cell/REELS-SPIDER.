"use client";

import { useState } from "react";

type Channel = { id: number; account_name: string; is_active: number };
type Reel = { channel_id: number; remote_post_id: string; caption: string | null; permalink: string | null; account_name: string };
type Rule = {
  id: number; channel_id: number; media_id: string; keywords: string[];
  link_url: string | null; private_reply_text: string | null; public_reply_text: string | null;
  enabled: number; last_polled_at: string | null; last_error: string | null; account_name: string;
  sent_count: number; unknown_count: number; last_send_error: string | null;
};

export function InstagramCommentRules({ channels, reels, initialRules }: {
  channels: Channel[]; reels: Reel[]; initialRules: Rule[];
}) {
  const [rules, setRules] = useState(initialRules);
  const [channelId, setChannelId] = useState(String(channels[0]?.id ?? ""));
  const [mediaId, setMediaId] = useState("");
  const [keywords, setKeywords] = useState("تم، الرابط، التفاصيل");
  const [link, setLink] = useState("");
  const [privateText, setPrivateText] = useState("أهلًا {username} ❤️ هذا الرابط: {link}");
  const [publicText, setPublicText] = useState("تم ❤️ أرسلت لك التفاصيل على الخاص");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function createRule(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setNotice("");
    try {
      const response = await fetch("/api/instagram-comment-rules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_id: Number(channelId), media_id: mediaId.trim(),
          keywords: keywords.split(/[،,\n]/).map((v) => v.trim()).filter(Boolean),
          link_url: link.trim(), private_reply_text: privateText,
          public_reply_text: publicText,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "تعذر حفظ القاعدة");
      setRules((current) => [data.rule, ...current.filter((r) => r.id !== data.rule.id)]);
      setMediaId(""); setNotice("تم حفظ القاعدة. شغّلها عندما تكون مستعدًا.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "حدث خطأ");
    } finally { setBusy(false); }
  }

  async function toggle(rule: Rule) {
    const response = await fetch("/api/instagram-comment-rules", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: rule.id, enabled: !rule.enabled }),
    });
    const data = await response.json();
    if (!response.ok) { setNotice(data.error || "تعذر تحديث القاعدة"); return; }
    setRules((current) => current.map((r) => r.id === rule.id ? { ...r, enabled: data.enabled ? 1 : 0 } : r));
  }

  return (
    <div className="max-w-4xl space-y-6">
      <section className="rounded-xl border border-border bg-surface p-5">
        <h2 className="text-base font-semibold text-ink">إضافة قاعدة لريل</h2>
        {channels.length === 0 ? <p className="mt-3 text-sm text-muted">أضف حساب Instagram أولًا من صفحة Channels.</p> : (
          <form onSubmit={createRule} className="mt-4 grid gap-4">
            <label className="grid gap-1 text-sm">حساب Instagram
              <select className="rounded-lg border border-border bg-surface px-3 py-2" value={channelId} onChange={(e) => { setChannelId(e.target.value); setMediaId(""); }}>
                {channels.map((c) => <option key={c.id} value={c.id}>{c.account_name}{c.is_active ? "" : " (غير نشط)"}</option>)}
              </select>
            </label>
            <label className="grid gap-1 text-sm">اختر ريلًا من المنشورات المتزامنة
              <select value={mediaId} onChange={(e) => setMediaId(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2">
                <option value="">أو أدخل معرّف الريل يدويًا أدناه</option>
                {reels.filter((reel) => reel.channel_id === Number(channelId)).map((reel) => (
                  <option key={reel.remote_post_id} value={reel.remote_post_id}>
                    {(reel.caption || reel.remote_post_id).slice(0, 65)} · {reel.remote_post_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm">معرّف الريل (Media ID)
              <input required value={mediaId} onChange={(e) => setMediaId(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2" placeholder="أدخل معرّف الريل من Meta" />
            </label>
            <label className="grid gap-1 text-sm">الكلمات التي تشغّل الرد
              <input value={keywords} onChange={(e) => setKeywords(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2" />
            </label>
            <label className="grid gap-1 text-sm">الرابط المرسل
              <input type="url" value={link} onChange={(e) => setLink(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2" placeholder="https://..." />
            </label>
            <label className="grid gap-1 text-sm">رسالة الخاص
              <textarea required rows={3} value={privateText} onChange={(e) => setPrivateText(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2" />
            </label>
            <label className="grid gap-1 text-sm">الرد العلني على التعليق
              <textarea rows={2} value={publicText} onChange={(e) => setPublicText(e.target.value)} className="rounded-lg border border-border bg-surface px-3 py-2" />
            </label>
            <p className="text-xs text-muted">المتغيرات المتاحة: {"{username}"} و{"{link}"} و{"{media_id}"}.</p>
            <button disabled={busy} className="w-fit rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy ? "جارٍ الحفظ..." : "حفظ القاعدة"}</button>
          </form>
        )}
        {notice ? <p role="status" className="mt-3 text-sm text-ink-soft">{notice}</p> : null}
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-ink">القواعد المحفوظة</h2>
        {rules.length === 0 ? <p className="rounded-xl border border-border p-5 text-sm text-muted">لا توجد قواعد حتى الآن.</p> : rules.map((rule) => (
          <article key={rule.id} className="rounded-xl border border-border bg-surface p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><p className="font-medium text-ink">{rule.account_name}</p><p className="mt-1 text-xs text-muted">Reel ID: <code>{rule.media_id}</code></p></div>
              <button onClick={() => toggle(rule)} className="rounded-lg border border-border px-3 py-2 text-sm">{rule.enabled ? "إيقاف القاعدة" : "تشغيل القاعدة"}</button>
            </div>
            <p className="mt-2 text-sm text-muted">الكلمات: {rule.keywords.join("، ")}</p>
            <p className="mt-1 text-xs text-muted">آخر فحص: {rule.last_polled_at ? new Date(rule.last_polled_at).toLocaleString() : "لم يبدأ بعد"}</p>
            <p className="mt-1 text-xs text-muted">الردود الناجحة: {rule.sent_count} · نتائج تحتاج مراجعة: {rule.unknown_count}</p>
            {rule.last_error ? <p className="mt-2 text-xs text-red-600">خطأ فحص: {rule.last_error}</p> : null}
            {rule.last_send_error ? <p className="mt-2 text-xs text-red-600">آخر خطأ إرسال: {rule.last_send_error}</p> : null}
          </article>
        ))}
      </section>
      <p className="text-xs leading-relaxed text-muted">ملاحظة: التشغيل يحتاج صلاحيات Meta المناسبة للحساب، وميزة إرسال رد خاص قد تكون غير متاحة قبل اعتماد التطبيق والصلاحيات. لا يتم إرسال أي شيء حتى تفعّل القاعدة.</p>
    </div>
  );
}
