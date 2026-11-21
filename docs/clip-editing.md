# Clip Editor

Clip Editor makes new reviewable copies and never replaces an original.

## Edit a downloaded source

After the download completes, select its job and choose **Downloaded source**. This is available while transcription and clipping continue, as well as after the job finishes. Scrub FFmpeg-extracted frames, set a trim range, choose a composition, and render; the new clip appears in **All Videos** for review. A source longer than 60 seconds starts with a 60-second trim to avoid an accidental full-VOD render.

## Standard edits

Choose a finished job and one of its original clips, then set the trim range, output aspect ratio and crop position, audio level, or playback speed. Rendered copies return to **All Videos** with `pending_review` status.

## Gaming / Reaction layout

Choose **Gaming / Reaction split**. The preview switches to FFmpeg-extracted frames from the retained original source. Select **Webcam** or **Game**, then drag a box around that region in the frame. Choose which panel goes on top and set the webcam's share of the vertical output from 25% to 50%. The renderer crops and scales each box without stretching, stacks them into a 9:16 frame, and preserves the selected audio and timing.

## Podcast two-shot

Choose **Podcast two-shot**, then draw one box for **Speaker A** and one for **Speaker B**. The two regions are rendered as equal-height panels in a vertical output; choose their order with **Top panel**. This is a fixed two-shot composition, not automatic speaker detection or camera switching.

## Requirements and limits

Layouts use the original `source.*` file, or a live job's `recording.ts`. If the source is missing, standard edits remain available but split layouts are not. The boxes are selected manually for the visible frame; Clip Editor does not identify a streamer, distinguish game characters from people, track camera moves, or check platform safe areas. Podcast mode does not use TalkNet or automatically cut between camera angles. Moment selection and clip scoring are unchanged.