# Clip Studio Publishing

Clip Studio publishes approved clips directly to configured YouTube, Instagram, Facebook, or X accounts. Choose **Publish…** to publish immediately or schedule through Agenttube's local queue. A clip must finish processing and be approved before publishing actions are available.

## Schedule a Post

Select one or more configured platforms, enter a future time in Asia/Kolkata, and choose **Schedule selected**. Agenttube stores the post in `data/clips/scheduled-posts.json`, displays it in the **Social Publishing** tab, and lets you cancel it before it is due. At the scheduled time, Agenttube uploads the clip directly to each selected platform and stores successful post links in the clip history. The queue runs in the dashboard process, so keep Agenttube running while posts are due. On restart, interrupted uploads are marked failed rather than automatically retried; verify platform status before manually scheduling again.

Immediate publishing and scheduled publishing use Agenttube's direct platform integrations. No TryPost service, API URL, or TryPost API key is required. Drafts on platforms that do not provide a direct draft API are not supported.

## Configure Accounts

Add only the settings for the platforms you use to the project-root `.env` file, then restart Clip Studio. Never put access tokens in the browser or commit them to Git.

### YouTube

1. In Google Cloud, enable the YouTube Data API v3 and create an OAuth client with type **Desktop app**.
2. Save its JSON file in the project, normally as `studio/client_secret.local.json`, or set `CLIPPER_YOUTUBE_CLIENT_SECRET` to its path in `.env`.
3. On the first YouTube publish, a browser window asks you to authorize the channel. The refreshable token is stored locally under `data/clips/.publishing/`.

The publishing dialog defaults to **Private**. Choose Unlisted or Public explicitly when appropriate.

### Instagram and Facebook

Both destinations use a Meta access token. Instagram publishing requires an Instagram **professional** account and its account ID. Facebook publishing requires a Page ID and a Page access token. Add:

```dotenv
CLIPPER_META_ACCESS_TOKEN=...
CLIPPER_INSTAGRAM_USER_ID=...
CLIPPER_FACEBOOK_PAGE_ID=...
CLIPPER_META_GRAPH_VERSION=v27.0
```

Create a Meta app and grant the token publishing access. For Facebook Login this normally includes `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, and `instagram_content_publish`. Meta may require app review or Page Publishing Authorization. The Instagram destination publishes Reels; Facebook publishes Reels to a Page, with a 90-second limit.

### X

Create an X developer app and generate a user-context OAuth 2.0 access token with `media.write` and `tweet.write` scopes. Add it to `.env`:

```dotenv
CLIPPER_X_USER_ACCESS_TOKEN=...
```

X developer access and account video limits apply. The post text is the title, followed by the optional caption.

## Publish a Clip

In **All Videos**, select a finished job, approve the clip, then choose **Publish…** on its card. Review the title, add an optional caption, choose one or more configured platforms, and confirm the YouTube privacy setting. The caption starts blank so Clip Studio's source permission record is not sent by accident.

Uploads run sequentially. Each platform reports success or its own error; a successful post's link is saved on the clip card. A failure on one platform does not undo a post already published elsewhere. Retry failed platforms individually after fixing their configuration.

Use only footage you own or have permission to publish. Platform API approval, account eligibility, quotas, and video requirements remain controlled by each platform.