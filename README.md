<img align="center" alt="head" src="./kitsu_readme.jpg" />

Kitsu is a Python Discord bot backed by MongoDB. The current dependency set is
tested with Python 3.10.

Large features are organized into packages under `app/cogs`, `app/services`,
and `app/database/repositories`. Each package separates commands, views, or
storage operations while keeping the existing public import and extension paths.
Tests are grouped by behavior; `tests/test_structure.py` enforces a 400-line
limit for Python modules and checks that the reorganized commands register.

Install runtime dependencies with `python -m pip install -r requirements.txt`.
This includes translation dependencies through `requirement-ml.txt`, which
currently selects the PyTorch CUDA 11.8 build. Translation model weights are
downloaded on first use. For vocabulary maintenance and model training/evaluation,
install `requirements-tools.txt` instead.

Create a local `.env` containing `DISCORD_TOKEN`, `DISCORD_APPLICATION_ID`,
`DISCORD_CLIENT_ID`, `DISCORD_MAIN_GUILD_ID`, and `DATABASE_URL`. Ghosty result
transactions require MongoDB configured as a replica set or sharded cluster.
Enable the Server Members and Message Content privileged intents in the Discord
developer portal, then start the bot with `python run.py`.

Channel configuration requires Manage Channels; translation configuration and
prefix changes require Manage Server. Prefix lookup and manual translation remain
available to members. Adding server emojis requires Create Expressions for both
the member and the bot, including when using a public preview. Temporary-channel
management also needs the bot's channel and permission-management permissions.
In the temporary-channel category, give the bot View Channel, Manage Channels,
and Manage Permissions (the channel form of Manage Roles). It also needs Send
Messages, Embed Links, and Read Message History for the text settings panel.
Administrator is not required. Category overrides can restrict the bot even when
its server role grants these permissions.

Temporary channels inherit the category's permissions at creation, then deny Use Application
Commands for members, including the creator and invited members. Visibility and
membership changes preserve that default. Owners manage their channel with
`<prefix>ghosty tempchannel settings` (for example,
`,k ghosty tempchannel settings`), then use the panel's buttons and menus. No
Manage Server permission is required for this owner-only panel. Opening it also
updates older channels to the command policy. Discord administrators bypass
channel permission overwrites.

The settings panel uses a mint Components V2 container with its dropdowns inside
and the delete button below. To use a custom thumbnail, place a square WebP at
`assets/tempchannel-settings.webp`; otherwise the panel displays the server icon.
The bot needs Attach Files to upload a custom thumbnail.

Administrators manage sticky messages with `/sticky add`, `/sticky edit`,
`/sticky remove`, `/sticky toggle`, and the Components V2 `/sticky list` panel.
Adding or editing a sticky (including the panel's modal) updates it immediately.
Every replacement deletes the old copy before posting. If deletion fails, no
new copy is sent. Enabling an already-present sticky reuses its existing copy.
Recovery also reuses a delivered message whose ID was not saved before a restart.
Channel locks serialize commands and message events, and queued events older
than the latest sticky do not cause redundant reposts.

Stickies repost after members' messages and, when **Ignore bots** is off, other
bots' messages. Kitsu's own messages never trigger reposts. The existing Ignore
bots setting is preserved when editing a sticky. On startup and every 30
seconds, recovery checks for deleted/missing stickies, pending cleanup, and
eligible activity among the latest 100 messages after the saved sticky. Quiet
channels are left alone. The bot needs View Channel, Send Messages, Read Message
History, and Embed Links for embed scripts; it can delete its own sticky without
Manage Messages. Missing permissions and invalid scripts are reported before
replacing an existing message. The list paginates ten channels per page.

The Ghosty dashboard has three messages: the tracker heading, the live market
card, and the Terms/Policy/settings controls. The 60-second refresh edits only
the existing live card, leaving the heading and controls in place. If the stack
is missing or incomplete, the bot posts it automatically. Saving or restoring
the channel-list settings reposts the full stack with the latest selection.
Hit notifications and new messages from members, including moderators and
administrators, move the stack below the new messages. The bot prepares the
views, deletes its old three messages, and then posts the complete stack again;
notifications and member messages stay in place. Reposts share a per-server
lock and combine pending requests. A message arriving during a repost triggers
another move so the stack stays together. Once it settles, the 60-second refresh
returns to editing only the middle card. The bot ignores its own stack messages
to prevent repost loops. Failed reposts are retried on the next refresh.
Every newly recorded live hit or miss also queues a live-card refresh,
including results in other channels and channels using hit-only notifications.
Burst updates share one refresh job and trigger a follow-up when newer results
arrive during its work, keeping the dashboard current without queuing copies.

Confirmed misses post a `#F5276F` tracker embed in the gambling channel with its
current streak, total loss, estimated hit position, and next-gamble probability.
The compact tracker omits the server/channel heading, title, footer, and timestamp.
Predictions appear on separate lines as **Estimated hit @** followed by a code-formatted
attempt number and **Next-gamble chance** followed by a code-formatted percentage.
Its thumbnail uses the application emoji `ghosty_track` (`1564714687084634274`).
Emoji metadata is preloaded at startup; building the thumbnail URL requires no
Discord request during tracker delivery.
The values come from the same prediction service and sample threshold as the
dashboard. Channels without enough history show "Still learning". The bot sends
the replacement only after deleting the previous tracker. A failed deletion
blocks the new send. A single active message ID is stored on the existing
channel-stat document, so replacement works
after restarts without a separate collection. A hit detaches that ID, preserving
the final tracker even when the next streak starts. Raw wager commands alone do
not change tracked statistics; Ghosty's confirmed result is required.

**Estimated hit @ selects from a fixed set of possible outcomes.** The shared
model builds candidates from hit positions observed between recorded hits in that
channel, using other channels' results as a smoothing prior. A channel without
known complete runs can borrow the shared set once its sample threshold is met.
The model saves up to five outcomes with the highest estimated probabilities.
For example, if the saved outcomes are attempt 8 at 20%, 13 at 40%, and 22 at 10%,
the dashboard first shows 13 at 40%, then 22 at 10% after thirteen misses.
The dashboard selects the highest-probability candidate still ahead and sorts
channels by that candidate's percentage. It can select a later attempt over an
earlier one when the later candidate has a higher probability.

The model counts hits at each attempt and how many observed runs reached that
attempt. Unfinished runs contribute their observed misses without inventing a
hit; the first partial run is excluded because its actual start is unknown.
Smoothed hazards are converted to exact-hit probabilities using the product of
preceding survival probabilities. This follows the discrete survival approach
described by [NIST](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/kaplan.htm),
with additional smoothing and partial pooling rather than an unmodified
Kaplan–Meier estimator. Validated context effects can adjust this curve.
Observed outcomes beyond attempt 200 remain eligible; only context features
are capped. A flat per-gamble rate no longer forces every channel's top target
to be attempt 1.

Candidate probabilities include all the preceding misses needed to reach that
exact next-hit attempt. They retain their original values when candidates pass;
the saved entries do not have to total 100%, since other outcomes remain possible.
Only the selected candidate is rendered in the tracker and dashboard:
`Estimated hit @` shows the attempt alone, and `Next-gamble chance` shows that
candidate's saved percentage. The `ghosty streak` Current Streak graph also shows
the saved alternatives. For example, candidates
2 at 8%, 4 at 9%, and 8 at 4% render only 4 with 9%, then 8 with 4% after four
misses. A hidden early hit is recorded and used for learning normally.

After the final candidate misses, the tracker shows `Estimated hit @` followed
by `was 8 MISSED` and `Next-gamble chance` followed by `was 4%` in this example.
It preserves the missed candidate's percentage; the dashboard adds `×`.
No additional candidates are invented for that streak.
A hit, including one earlier than the selected target, ends the streak and clears
the set. Manual streak resets also invalidate it. The actual early hit or missed
attempt remains in recorded training history; the 60-second worker incorporates
these results into future models. Learning does not rewrite an issued set.

Candidates and their probabilities are saved once on the existing channel-stat
document, survive restarts, and are shared by every display. New forecasts only
include future attempts. Legacy single-target forecasts are replaced once with
the new candidate format on the next refresh. Version 2 forecasts from the old
geometric ranking are also replaced once with version 3 history-based candidates.
No additional channel rows are created.

Tracker delivery does not wait for dashboard requests, model training, or
old-message deletion. Predictions use the latest published model with the newly
committed channel counters and live channel context; cold startup uses the
channel's historical rate until a model is ready. Training weights remain
immutable while live context advances, and results received during training are
replayed after publication. Dashboard predictions reuse their already-loaded
channel statistics. Normal Ghosty responses and cached commands avoid a Discord
history fetch, and the repository performs the single legacy/deduplication check.
`Ghosty result recorded` and `Ghosty tracker posted` log recording/delivery times
in milliseconds so database or Discord delays can be diagnosed separately.

Miss trackers are enabled by default. An explicitly saved
`/ghosty notify mode:hit_only` preference is respected; use
`/ghosty notify mode:always` to enable trackers in that channel. The bot needs
Send Messages and Embed Links in channels where it posts tracker/hit embeds.

Hit summaries select a random local reaction from
[`assets/ghosty_reactions.json`](assets/ghosty_reactions.json): 100 win messages,
100 loss messages, and a neutral break-even message. The JSON is loaded once
at startup and cached in memory; restart the bot after editing it. Selection
uses the completed streak's net result, so a payout that leaves an overall loss
uses the loss bank. A missing or invalid file uses small local defaults.
The complete reaction is included in the first send, with no model generation
or GPU use. The final mint (`#7CC6B0`) embed
shows **Streak win/loss** and the verified net amount, with a short reaction on
the very next line. One blank line separates the reaction from **Next Estimated
Hit @**, which shows the highest-probability candidate for the new streak, or
"Still learning" when there is not enough history. Only the estimate initially
shows the application's `loading` emoji and "Learning from this hit…" on their
own line. The **Next Estimated Hit @** label appears when loading finishes. A background
task refreshes the shared numerical model with recorded results, saves the new
candidate set, and edits that line. The amount and random reaction stay intact.
The dashboard and subsequent tracker use the same saved candidate set; a new
streak does not freeze a stale estimate while its hit is still waiting to train.
Training failures replace the spinner with "Unavailable". If that next streak
has already ended, the old summary shows "Streak already ended". The embed has
no title, footer, timestamp, winner/payout, wager, or miss count.
An exactly zero net result is labeled break-even. Wins get a cheerful reaction;
losses get gentle sarcasm about Ghosty's supposed generosity, without mocking
the player or celebrating the loss. The final tracker remains in the channel.
The predictor below calculates the numerical estimates and learns from gambling
history independently of these preset messages.

Market-channel hit notifications remain plain text: the source channel and
completed-streak contributor mentions, a congratulations line crediting the
actual hitter, total streak/profit, and a link to Ghosty's result. Contributors
are deduplicated, stop at the previous hit or the current counter's streak
boundary, and exclude the winner from the first line (the winner is mentioned
in the congratulations). Celebrations in the source gambling channel still use embeds.
If the market channel itself receives a hit, it gets one plain-text notification.

Ghosty uses one shared model per server for every channel. Each channel keeps
its own context: creation time (derived from its Discord ID), age, whether it
was created since the **05:30 AM IST** reset, last recorded gamble time, time
since that gamble, and whether it was before the latest reset. The shared model
also considers the current miss streak, the channel's daily session streak,
channels under **24 hours** old, and attempts **5–15** after a completed streak
of **20 or more misses**. Training uses only that channel's earlier results to
build each example's context. Last-gamble timing is updated after the result's
features are built, preventing the result from supplying its own prior context.
These are hypotheses, not confirmed Ghosty mechanics or fixed luck bonuses.
Daily sessions reset only session context; recorded statistics and history are
preserved. A channel's last gamble remains available across a reset.

The model trains on up to 20,000 recent results per server. Context adjustments must improve both
log loss and Brier score against raw and pooled historical baselines in forward
validation, beat both baselines in each later time window, and improve combined
log loss by at least 1%. Binary
patterns require at least 30 matching observations and three hits, plus a
comparison group, in the earliest training window. Sparse features are ignored.
When validation fails, per-gamble estimates fall back to historical rates and
candidate ranking uses the smoothed streak history without context adjustments.
Channels below the sample threshold can use pooled estimates only when the
new-channel feature has enough training evidence and passes validation.
No extra ML dependency is required.

`/ghosty config` shows the current settings. Managers can set `minimum_samples`
and turn `pattern_predictions` on or off. Each server's local files live in
`data/ghosty/<server-id>/`:

- `training.json` contains the current training examples: channel/result IDs,
  timestamps, and hit/miss labels, with a dataset fingerprint.
- `model.json` contains the learned weights, feature settings, channel contexts,
  per-attempt hit/risk counts, validation scores, training time, and matching
  dataset fingerprint. Older model versions rebuild automatically.

The bot starts a background training job after it connects. Every 60 seconds it
checks for new recorded results, refreshes the dataset, and retrains when the
examples or pattern settings have changed. This works without opening the
dashboard. Unchanged datasets reuse the existing model, including after a bot
restart. Every newly trained model still has to pass forward validation before
its pattern adjustments are used; otherwise historical rates remain active.

MongoDB remains the full source of history; the local snapshot contains the most
recent 20,000 training examples. Files are saved atomically, and damaged or
incompatible models are rebuilt from database history. Models are also cached in
memory for 60 seconds; recorded results and configuration changes invalidate that
cache. The existing `data/` gitignore rule keeps generated training files local.

Dashboard and `/ghosty stats` reads never create empty channel-stat documents.
Only actual recorded results create/update channel statistics. The dashboard
lists existing, included channels with recorded activity and only saves its
page setting when it changes. Channel creation/reset/gamble features are derived
from IDs and history in memory and saved with the local model, without additional
MongoDB feature documents. Recording no longer writes unused per-user streak
snapshots; original history and its notification attribution are retained.

Members can run `<prefix>ghosty streak` for a compact Components V2 panel with
exactly two dropdown options:

- **Previous Attempts** opens first. It shows the last five completed Hit @
  values, newest first, followed by their lost amounts, payouts received, and
  net wins/losses in Goos. A hit position includes the winning gamble. Net amounts
  use the same payout-minus-miss-loss convention as hit summaries. History queries
  locate the preceding six hit boundaries, so long runs are not cut off at 500
  results. An unknown first partial run is excluded, as is the unfinished run.
- **Current Streak** shows the live streak/loss, the selected forecast, and a
  graph with one orbital-style lobe per saved possible hit attempt, up to five.
  Each lobe is labelled with its actual saved percentage; the selected outcome
  is mint and passed outcomes are grey with `×`. The graph does not fit a new
  single-peak channel-rate distribution or change frozen candidate probabilities.

The panel omits server/channel headings, overall Bayesian averages, theory text,
and page controls. Refresh reloads the chosen view; controls belong to the
requester and expire after five minutes. Old three-candidate forecasts remain
fixed for their current streak; new forecasts can contain up to five outcomes.
All reads stay within the invoked server/channel and create no additional rows.

Charts use Matplotlib's Agg backend and NumPy, installed through `requirements.txt`.
Rendering runs off the event loop. Current Streak needs Attach Files for its PNG;
Previous Attempts works without that permission and removes old chart attachments
when selected. No Ollama generation is used by the panel.

Members can run `<prefix>ghosty pause` in a channel included in the dashboard
after **five of their own confirmed gambles in its current miss streak**. A
Components V2 dropdown offers **5, 10, 15, or 30 minutes; 1, 3, 6, or 12 hours**.
Eligibility is checked again when a duration is selected. The saved server
prefix applies to both `ghosty pause` and `ghosty resume`.

The pause changes only **Send Messages**: ordinary members become read-only,
including members with explicit role/member write grants. The requester,
approved members, moderator role **1553874776219713566**, administrators, Kitsu,
and Ghosty retain writing access. Viewing and slash-command settings are
preserved. Only the owner or moderators can add/remove approved members or
resume early, using the persistent panel or `<prefix>ghosty resume`. The panel
shows Discord's relative expiry timestamp and the exact expiry time.

Original Send Messages values and the expiry are saved under the existing
channel statistics document; no extra channel or user documents are created.
An expiry worker checks every 15 seconds, restores the saved values, and
recovers pending permission changes after restarts or temporary failures.
Unrelated permission changes made during the pause are preserved. Kitsu needs
View Channel, Read Message History, Send Messages, Manage Channels, and Manage
Permissions in the channel. Gamble recording, predictions, and dashboard
refreshes continue throughout the pause, even after a hit; a lock marks the
channel in the dashboard. Existing history is verified against original Ghosty
responses for eligibility when player attribution was not previously verified.

`Hit @` is the highest-probability remaining saved outcome, with no guaranteed
winning attempt. The underlying per-gamble model also calculates an expected
position and 25–75% interval when available. Those values do not select the
displayed candidate. Forecasts assume further attempts occur under the current
session conditions; they do not predict a wall-clock hit time.

Run offline unit tests with `python -m pytest`. Tests mock database access and
Discord requests; they do not need a `.env`, a running MongoDB server, or model
downloads.
