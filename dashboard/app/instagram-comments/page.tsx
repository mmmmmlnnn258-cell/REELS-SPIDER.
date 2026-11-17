import { getChannels } from "@/lib/queries";
import { getDb } from "@/lib/db";
import { PageHeader } from "@/components/ui";
import { InstagramCommentRules } from "@/components/instagram-comment-rules";

export const dynamic = "force-dynamic";

export default function InstagramCommentsPage() {
  const channels = getChannels()
    .filter((c) => c.platform === "instagram")
    .map((c) => ({ id: c.id, account_name: c.account_name, is_active: c.is_active }));
  const reels = getDb().prepare(
    `SELECT rm.channel_id, rm.remote_post_id, rm.caption, rm.permalink, c.account_name
       FROM remote_media rm JOIN channels c ON c.id = rm.channel_id
      WHERE c.platform = 'instagram' AND c.is_active = 1
        AND rm.is_deleted = 0 AND rm.media_product_type = 'REELS'
      ORDER BY rm.published_at DESC LIMIT 300`
  ).all() as Array<{
    channel_id: number; remote_post_id: string; caption: string | null;
    permalink: string | null; account_name: string;
  }>;
  const rules = getDb().prepare(
    `SELECT r.id, r.channel_id, r.media_id, r.keywords_json, r.link_url,
            r.private_reply_text, r.public_reply_text, r.enabled, r.last_polled_at,
            r.last_error, c.account_name
       FROM instagram_comment_rules r
       JOIN channels c ON c.id = r.channel_id
      ORDER BY r.created_at DESC, r.id DESC`
  ).all() as Array<{
    id: number; channel_id: number; media_id: string; keywords_json: string;
    link_url: string | null; private_reply_text: string | null; public_reply_text: string | null;
    enabled: number; last_polled_at: string | null; last_error: string | null; account_name: string;
  }>;
  return (
    <div>
      <PageHeader title="Instagram comment automation"
        subtitle="Create a rule for a Reel. The worker checks comments and replies only when the rule is enabled." />
      <div className="px-8 py-6">
        <InstagramCommentRules channels={channels} reels={reels} initialRules={rules.map((r) => ({
          ...r, keywords: (() => { try { return JSON.parse(r.keywords_json) as string[]; } catch { return []; } })(),
        }))} />
      </div>
    </div>
  );
}
