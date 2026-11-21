# Agenttube

**A local dashboard for clipping, creating, reviewing, and managing YouTube video workflows.**

Clip Studio opens first: paste a video link or upload a file, manage creators, then review generated clips in **All Videos**. Native Video Generator, Clip Editor, and Social Publishing tabs handle original video creation, clip edits, and publishing without launching separate applications.

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Node.js 18.17+](https://img.shields.io/badge/node-18.17%2B-43853d.svg)](package.json)

![Agenttube Clip Studio Home with the clip composer and creator list](assets/screenshots/overview.png)

| ![All Videos showing job status and clips ready for review](assets/screenshots/review.png) | ![Clip Studio Home at a mobile viewport](assets/screenshots/operator.png) |
| :--: | :--: |
| All Videos: job progress and review queue | Clip Studio Home: responsive layout |

*Screenshots use browser-only demo creators and jobs. Run `npm run docs:screenshots` with the local dashboard running to regenerate them; it does not change saved creators or jobs.*

## Why it's different

- **One dashboard, distinct workflows.** Clip Studio, Viral Clone, Video Generator, Clip Editor, and Social Publishing share one review and publishing flow.
- **Review clips before you use them.** Clip Studio produces local files for review; it does not publish them to YouTube.
- **Local-first.** Dashboard services run on your machine, with outputs kept in their configured data folders.
- **Choose the right workflow.** Use Clip Studio for permitted source footage, Video Generator for narrated original videos, Clip Editor for post-render changes, and Automation Agent for channel planning.

## Quick start

For Clip Studio, you need Node.js 18.17 or newer, Python 3.10 or newer, and FFmpeg with libass installed. Create its isolated environment and start the dashboard:

```powershell
python -m venv .venv-clipper
.venv-clipper\Scripts\python.exe -m pip install -r clipper\requirements.txt
npm run clip:dashboard
```

On macOS or Linux, use `.venv-clipper/bin/python` in place of `.venv-clipper\Scripts\python.exe`. Open `http://127.0.0.1:8765`.

The dashboard opens on Clip Studio Home. Use **All Videos** to follow jobs and review clips. Optional tools may need their own dependencies and credentials; see the sections below.

## Dashboard tabs

| Tab | What it does |
| --- | --- |
| Clip Studio | Home has the URL/upload composer and creator list; All Videos shows job progress and clip review. |
| Viral Clone | Analyzes a reference video's structure and writes original script variants. |
| Video Generator | Turns an idea or script into a narrated, captioned video using Agenttube's local rendering workflow. |
| Automation Agent | Embeds the original channel automation dashboard when its service is running. |
| Video Studio | Shows status and logs for the original-video workflow and can open its interface separately. |
| Clip Editor | Trims and reframes clips, or builds manual Gaming/Reaction and Podcast split layouts from the retained source video; edits are new reviewable copies. |
| Social Publishing | Publishes directly to configured platforms and manages Agenttube's local post schedule. |

PeakCut is not a dashboard tab. Its source remains available separately in [`peakcut/`](peakcut/README.md).

## Automation Agent

The Automation Agent is the original YouTube channel-management application, running as a separate Node.js service on port 3456. Its dashboard tab shows service status and readable logs, and can start or stop the service. After installing dependencies, finish credential setup in a terminal from the Agenttube folder:

```bash
npm install
npm run credentials:setup
```

The setup wizard connects YouTube and configures an AI provider and channel settings. Keep API keys in the terminal, not the dashboard. Once setup is complete, click **Start service** in the tab. The Agent schedules content created in its own channel workflow; it does not automatically import or schedule files uploaded to Clip Studio. To publish or schedule a Clip Studio clip, approve it in **All Videos** and choose **Publish…**. Settings are documented in [`.env.example`](.env.example).

### YouTube credentials

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **YouTube Data API v3**, and **YouTube Analytics API** for retention and outcome data.
3. Create an OAuth 2.0 client of type **Desktop app** (not Web application) and save the JSON as `config/credentials.json`.
4. Under **Google Auth Platform → Audience**, add the Google account that owns your channel as a **test user**. Otherwise sign-in fails with `Error 403: access_denied`. While the app is in Testing mode, Google expires the sign-in after 7 days.

### Automation Agent workflow

| Stage | What the agent does | What you control |
| --- | --- | --- |
| Research | Finds topics and builds a content strategy | Niche, audience, blocked topics |
| Script | Writes the hook, narrative, CTA, and metadata | Voice, format, length, brand direction |
| Production | Generates narration and visuals, then assembles a real MP4 | Provider choice and media fallbacks |
| Review | Runs quality checks and opens the video for review | Facts, media rights, edits, approval |
| Publish | Schedules and uploads approved content | Privacy, timing, final decision |
| Learn | Captures 24-hour and 7-day evidence, measures your chosen outcome, then proposes the next move | Choose the KPI and approve or reject each lesson |

### Automation Agent features

Each item links to its walkthrough in the [feature guide](docs/features.md).

**Plan**
- [Autonomous Channel Operator](docs/features.md#autonomous-channel-operator): give it an objective, audience, content pillars, cadence, and limits. It researches trends and competitors, builds an evidence-labeled plan, and sends each video through production.
- [Outcome & ROI Studio](docs/features.md#outcome--roi-studio): set a target (views, watch hours, net subscribers, engagement, or revenue) and a budget, and track progress. Missing data shows as unavailable, never as zero.

**Produce**
- [Production readiness](docs/features.md#production-readiness): a verified check of every provider, your channel, and video assembly before anything runs on its own. Paid image and video probes are separate opt-ins.
- [Resumable generation](docs/features.md#resumable-generation): every stage is checkpointed, so a crash or timeout resumes where it stopped instead of starting over.
- [AI video generation](docs/features.md#ai-video-generation): local slideshow by default; Seedance, MiniMax H3, Gemini Omni, Kling, or Wan for real clips, with a cap on paid seconds.
- [Shorts Repurposing Studio](docs/features.md#shorts-repurposing-studio): turn an approved video into three 9:16 Short drafts with burned-in captions, reusing existing media so no new generation credits are spent.

**Review**
- **Content review:** quality checks, metadata editing, approval, and scheduling for each video.
- [Scene Repair Studio](docs/features.md#scene-repair-studio-and-narration): edit, reorder, lock, replace, or regenerate a single scene, then rebuild the video.
- [Evidence desk](docs/features.md#evidence-desk): link each factual claim in the script to a verified source before approval.
- [Discoverability preflight](docs/features.md#discoverability-preflight): advisory search and AI-answer checks from a bundled DarkzSEO audit.

**Learn**
- [Learning loop](docs/features.md#learning-loop): compares each video's 24-hour and 7-day results with your channel's own history and proposes changes for you to approve.
- [Controlled growth experiments](docs/features.md#controlled-growth-experiments): test approved title and thumbnail variants on a live video. A winner needs a 95% evidence threshold, and the original is restored before you decide.
- [Scene-aware retention](docs/features.md#scene-aware-retention): see which scene lost viewers.

**Engage**
- [Audience Engagement Studio](docs/features.md#audience-engagement-studio): comment sync, themes and questions, spam quarantine, reply drafts that post only after you approve them, and video ideas mined from repeated requests.

## Clip Studio

Turn a long video (yours, or another creator's **with their permission**) into review-ready clips: 9:16 Shorts under 60 seconds with a face-centred crop, word-by-word captions and a hook, plus 7–10 minute 16:9 clips. Speech is transcribed on your machine with faster-whisper; Gemini picks and scores the moments (hook, engagement, value, shareability), falling back to the loudest stretches when no AI is available. Nothing is uploaded: each job writes the clips and a `manifest.json` (credit, permission record, titles, descriptions, scores) with every clip marked `pending_review`.

```bash
python -m venv .venv-clipper
.venv-clipper/Scripts/pip install -r clipper/requirements.txt   # macOS/Linux: .venv-clipper/bin/pip
npm run clip:dashboard   # then open http://127.0.0.1:8765
```

**Dashboard.** Home contains the URL/upload composer and creator list. Save creators you have permission to clip (channel link, permission record, niche), press **Check live** to see who is streaming, then **Clip live now** or **Clip a video**. **All Videos** shows job progress and opens the review view; each clip plays with its titles, alternative titles and scores, and you can approve or reject it there. The dashboard listens on `127.0.0.1` only. Finished MP4s are saved in `data/clips/<job-id>/` (for example, `short_01.mp4` and `long_01.mp4`).

**Publish.** Approve a finished clip, choose **Publish…**, select one or more connected platforms, review its title and optional caption, then publish. YouTube privacy defaults to private. Configure each account in `.env` first; see the [publishing guide](docs/publishing.md). Published post links appear on the clip card.

**Live streams.** Live mode records the stream from now on and transcribes it in 5-minute windows while it plays, cutting Shorts as highlights happen (`--live-shorts`, 0 = only after the stream). When the stream ends or you press **Stop**, long clips are made from the full recording. It uses the faster `base` Whisper model by default to keep up on a CPU.

**Viral Clone & Create tab.** Paste a video that performed well and a topic: it is transcribed locally, Gemini maps its structure (hook, beats, pacing, payoff, CTA) and writes new scripts that follow the same structure for your topic. Only the structure is reused, never footage or sentences, and the downloaded audio is deleted afterwards. Inspired by [Hypit](https://github.com/hypit-ai/hypit)'s "clone any viral video" idea; independently implemented. CLI: `npm run clip -- <url> --clone-topic "your topic" --variants 3`.

**Create a video (free, any topic).** Paste your own script (narrated exactly as written), describe an idea (Gemini's free tier writes the script), or press **Make video** on a clone variant. Each scene gets a free-licence Wikimedia Commons image with a slow camera move, a look (documentary, studio, blueprint, night), on-screen words that land on the spoken word, word-by-word captions, a synthesised whoosh between scenes and loudness-normalised audio. The voice is Microsoft Edge TTS when reachable, otherwise the offline Windows voice; word timing comes from local Whisper, so any voice works. Every job writes `credits.md` and an SRT, and attribution goes into the description. Method adapted from [cinematic-skill](https://github.com/Moh4696/cinematic-skill) (MIT). CLI: `npm run clip -- script.txt --create script --format short --look documentary`.

**Inspiration.** Search 700+ viral video prompts from [awesome-opus5-5-videos](https://github.com/yihui-dev/awesome-opus5-5-videos) (fetched live; every prompt links to and credits its creator) and use one as the idea for a new video.

**CLI.** The same clipping and live-stream jobs are also available from the command line:

```bash
npm run clip -- "https://www.youtube.com/watch?v=..." --permission "email from @creator, 2026-11-19" --shorts 5 --long 1 --niche trading
npm run clip -- "https://www.youtube.com/@creator" --live --permission "email from @creator, 2026-11-19" --live-shorts 1 --long 1
```

Needs FFmpeg with libass on `PATH`. Output goes to `data/clips/<job-id>/`. Run `npm run clip -- --help` for all options; `--model` also accepts a local faster-whisper folder for networks that block model downloads. Moment selection and live clipping are inspired by [PeakCut](https://github.com/haziqahmedqasmi/PeakCut) (MIT); per-clip title variants by [Hypit](https://github.com/hypit-ai/hypit).

**Automation Agent tab.** Shows the original YouTube Automation Agent dashboard (port 3456) when its separate service is running, along with status, logs, and an **Open in new window** link. Start it from the Agenttube folder with `npm start`; setup requires `npm install` and the walkthrough above. The tab itself has no Start/Stop controls.

**Video Studio tab.** Shows the migrated original-video workflow's status and logs and provides an **Open in new window** link. It covers topic research, script writing, narration, stock visuals, rendering, metadata editing, review, and optional YouTube upload. Install its isolated dependencies once, then start the standalone service:

```powershell
python -m venv .venv-studio
.venv-studio\Scripts\python.exe -m pip install -r studio\requirements.txt
.venv-studio\Scripts\python.exe studio\gui.py
```

Then open the **Video Studio** tab in Agenttube or visit `http://127.0.0.1:7070`. Studio settings and OAuth files stay in the ignored `studio/.env` and `studio/client_secret*.json` files; generation does not upload unless you explicitly choose Upload in the studio. Its separate virtual environment keeps MoviePy 1.x and NumPy below 2 isolated from Clip Studio.

**PeakCut (standalone).** PeakCut is not a tab in the Agenttube dashboard. Its source and MIT license remain in [`peakcut/`](peakcut/README.md), and its local data stays in `peakcut-data/`.

**Video Generator and Clip Editor tabs.** Both use Agenttube's own Python/FFmpeg pipeline and the existing Clip Studio job store. Generated and edited videos appear in **All Videos**, where the same approval and direct publishing controls apply. No MoneyPrinterTurbo service or Docker setup is required.

**Social Publishing tab.** Configure YouTube OAuth, Meta publishing credentials, or an X user token using the environment variables in [`.env.example`](.env.example). Scheduling is stored in `data/clips/scheduled-posts.json` and runs in the Agenttube dashboard process; keep the dashboard running at the scheduled time. The queue is local to this machine, and no third-party post-management service is required.

## Automation Agent providers

One key gets you started. Pick a text provider in the walkthrough: Gemini, OpenAI, OpenRouter (400+ models, including Claude), Kimi, MiMo, GLM, DeepSeek, or MiniMax. Gemini has free tiers for text and narration.

- **Narration** comes from ElevenLabs, OpenAI, or Gemini, in that order of preference. A video without working narration can't be approved.
- **Images** come from OpenAI or Gemini, each falling back to the other.
- **Video** is a free local slideshow by default. Choose Seedance, MiniMax H3, Gemini Omni, Kling, or Wan for generated clips and set a cap on paid seconds; if a provider fails, the video falls back to the slideshow.
- **No-key options:** set `EDGE_TTS_ENABLED=true` (needs Python `edge-tts`; optional `EDGE_TTS_PYTHON`, `EDGE_TTS_VOICE`) for free narration, and `IMAGE_PROVIDER=pexels` with `PEXELS_API_KEY` for licensed stock stills. Pexels is also the fallback when no image model works, and its source and license stay in the scene evidence.

Models, keys, env vars, clip limits, and the `.env` essentials are in the [providers guide](docs/providers.md).

## Automation Agent schedule

The scheduler starts with `npm start` and uses the server's local time.

| When | What runs |
| --- | --- |
| Daily at 06:00 | Content generation, following the active channel strategy's cadence |
| Every 15 minutes | Publishing queue |
| Daily at 09:00 | Analytics collection |
| Daily at 22:00 | Optimization |
| Every 4 hours | Comment sync and growth-experiment refresh |
| Sundays at 08:00 | Weekly strategy review |

Details are in the [feature guide](docs/features.md#automation-schedule).

## Docs

- [Feature guide](docs/features.md): a walkthrough of every studio and gate.
- [AI providers](docs/providers.md): text, narration, image, and video providers with keys and limits.
- [API reference](docs/api.md): every HTTP route the dashboard server exposes, for example `POST /generate` with `x-api-key`.
- [Social publishing](docs/publishing.md): configure YouTube, Instagram, Facebook, and X uploads from Clip Studio.
- [Troubleshooting](docs/troubleshooting.md): fixes for setup, sign-in, and generation problems.
- [Changelog](CHANGELOG.md) and [Agenttube releases](https://github.com/imrkbhai/Agenttube/releases).

The three problems people hit most:

| Problem | Fix |
| --- | --- |
| `Missing credentials for: an AI provider` | Configure any one text provider with `npm run walkthrough`. OpenAI isn't required. |
| Google sign-in shows `Error 403: access_denied` | Add your Google account as a test user under **Google Auth Platform → Audience**. |
| No video is produced, or it's marked simulated | Read the ✗ lines in the startup capability check, and make sure you ran `npx playwright install chromium`. |

Questions and setup help go in [Agenttube Discussions](https://github.com/imrkbhai/Agenttube/discussions).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for ground rules, project structure, and how to add a provider. Short version: one focused concern per PR, no lockfile churn, lint and tests must pass. Issues are for bugs.

```bash
git clone <your-fork>
cd youtube-automation-agent
npm install
npm run lint   # CI runs this on every PR
npm test
```

Made something real with it? Share verified output in [examples](examples/README.md).

## Built by

[@darkzOGx](https://github.com/darkzOGx), a solo builder shipping AI automation and developer tools. Find me on [X](https://x.com/darkzOGx) and [laderalabs.io](https://laderalabs.io). If YouTube Automation Agent saves you time, a star helps it reach more developers.

## License

MIT. See [LICENSE](LICENSE). Model and API acknowledgments are in the [providers guide](docs/providers.md#acknowledgments).

> This tool is for legitimate content creation. Comply with [YouTube's Terms of Service](https://www.youtube.com/t/terms) and Community Guidelines.

---

More from darkzOGx:

- [darkzloop](https://github.com/darkzOGx/darkzloop): terminal agent runner that turns any LLM into a disciplined software engineer (FSM control, model-agnostic, BYO auth)
- [darkzBOX](https://github.com/darkzOGx/darkzBOX): open-source Instantly.ai clone with smart automated email replies
- [open-sales-researcher](https://github.com/darkzOGx/open-sales-researcher): autonomous B2B company research. Works with Claude Code, Cursor, Copilot.
- [darkzseo](https://github.com/darkzOGx/darkzseo): SEO tooling
- [ConstructionBids.ai](https://constructionbids.ai): AI scans every federal, state & local public works bid and matches you to contracts you'll win.
