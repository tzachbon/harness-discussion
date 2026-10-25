# Check as you type: release check

Run on Windows and on a Mac from this branch, then fill in the tables. Every row must pass before the PR leaves
draft (issue #99, Definition of Done 1–3 and 5). CI covers DoD 4.

## Set up (each OS)

1. Quit the installed Harness Discussion first; only one copy runs at a time.
2. In a checkout of `feat/live-check`: `uv sync`, then `uv run harness-discussion`.
3. Settings → Writing: Writer on and **Default mode** = Fix errors. **Check as I type** is on by default; if it is
   off, turn it on and confirm.
4. Open the log in a second window. Windows: `%APPDATA%\harness-discussion\harness-discussion.log`.
   Mac: `~/Library/Application Support/harness-discussion/harness-discussion.log`.
5. Open [`spikes/fixtures/live/page.html`](../../spikes/fixtures/live/page.html) from disk in each browser below.
   It holds one line per language with known mistakes; copy lines from it.

## One cell

Use an empty field or a new line. A line is checked only after you type or paste into it, so: click into the field,
wait a second, paste the line, and wait for the underlines (usually 2–6 seconds). In Slack, use your self-DM and
never press Enter.

| Check | Pass when |
| --- | --- |
| a | Each mistake gets a red underline. If underlines can't be placed, a count badge appears at the field's top right instead; write **badge** in the Mode column. |
| b | Clicking an underline shows a card with Apply, Dismiss, Review and More. (Badge: clicking it opens Review.) |
| c | **Apply** changes only that word or span; the rest of the line is unchanged. (Badge: Review's **Replace** changes only that line.) |
| d | **Undo** on the toast restores the original line. |
| e | Pasting elsewhere afterwards still gives the line you copied: the clipboard was kept. |

Apply all: with underlines on two lines and nothing selected, press the rewrite shortcut (Ctrl+Shift+E, ⌥⌘E). Both
lines are fixed, no new `live check` line is logged, and the toast says how many changes were applied. Record it in
the Mode column of the en cell.

Checking strip: about 0.7 seconds after you stop typing, a faint grey line sits under the line being checked until
its result arrives.

Rewrite shortcut with nothing selected (issue #120). Run each in a fresh line of the en cell's field:

| Case | Pass when |
| --- | --- |
| Underlines shown | Every underlined change is applied, as in Apply all above. |
| No underlines yet, even mid-check | The whole field is rewritten. The toasts read "Rewriting the whole field" and "Rewrote the whole field". |
| Field over 4,000 characters | Nothing is sent. The toast says to select text and that the field has more than 4,000 characters. |
| Shortcut bound to ⌘\` (Mac) | It fires in every app above, the same as ⌥⌘E. |

## Windows

| Target | en | he | ar | ru | es | fr | Mode | Screenshot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Notepad | | | | | | | | |
| Slack message box | | | | | | | | |
| Edge textarea | | | | | | | | |
| Edge text input | | | | | | | | |

## macOS

| Target | en | he | ar | ru | es | fr | Mode | Screenshot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TextEdit | | | | | | | | |
| Slack message box | | | | | | | | |
| Safari textarea | | | | | | | | |
| Safari text input | | | | | | | | |
| Chrome textarea | | | | | | | | |
| Chrome text input | | | | | | | | |

Mark each language cell PASS or FAIL (and which of a–e failed).

## Style mode (DoD 2)

Set **Default mode** to Improve writing, paste the page's *style* line into Notepad (Windows) or TextEdit (Mac),
and wait. Pass: underlines cover whole phrases or the sentence, and Apply replaces exactly the
underlined part. Set the mode back to Fix errors afterwards.

| OS | Result | Screenshot |
| --- | --- | --- |
| Windows | | |
| macOS | | |

## Safety (DoD 3), one session per OS

1. Positive control: run one Notepad/TextEdit cell. The log gains one `live check <n> chars` line and one
   `live check done with <source>` line, and never the text itself.
2. Note the count: `findstr /c:"live check" <log> | find /c /v ""` (Windows) or `grep -c 'live check' <log>` (Mac).
3. Type a sentence of 3 or more words into the page's **Password** field and wait 4 seconds. Do the same in a
   terminal (Windows Terminal, or Terminal on the Mac). The count is unchanged.
4. In Slack, click an underline → More → **Turn off in Slack**. All Slack underlines vanish; typing a new line in
   Slack adds no `live check` line. Settings → Writing lists Slack with **Turn on**.
5. Turn **Check as I type** off. All underlines vanish within a second, and typing adds no `live check` line.

| Step | Windows | macOS |
| --- | --- | --- |
| 1 Positive control | | |
| 3 Password field | | |
| 3 Terminal | | |
| 4 Turn off in Slack | | |
| 5 Feature off | | |

## CPU (DoD 5)

Paste the page's *en* line into Notepad/TextEdit 25 times, one per line (about 2,000 characters), and wait: one
check underlines every copy of the line. Then run the command below, click back into Notepad/TextEdit within
5 seconds (only the focused field is checked), and leave the keyboard and mouse alone:

Windows (PowerShell):

```powershell
function cpu($ids) { (Get-Process -Id $ids | Measure-Object CPU -Sum).Sum }
$app = (Get-CimInstance Win32_Process | Where-Object CommandLine -like '*harness-discussion*').ProcessId
$pad = (Get-Process notepad).Id
Start-Sleep 5; $a, $n = (cpu $app), (cpu $pad); Start-Sleep 60
"app {0:N1} %, Notepad {1:N1} % of one core" -f (((cpu $app) - $a) / 0.6), (((cpu $pad) - $n) / 0.6)
```

Mac (Terminal):

```sh
app=$(pgrep -nf 'harness[-_]discussion'); edit=$(pgrep -x TextEdit); sleep 5
for i in $(seq 60); do echo $(ps -o %cpu= -p $app) $(ps -o %cpu= -p $edit); sleep 1; done |
  awk '{a+=$1; e+=$2} END {printf "app %.1f %%, TextEdit %.1f %% of one core\n", a/NR, e/NR}'
```

Run it again with Check as I type off. Pass: the app averages 3 % or less with it on. Report it if Notepad or
TextEdit rises by more than 2 points.

| OS | App, on | Editor, off | Editor, on |
| --- | --- | --- | --- |
| Windows | | | |
| macOS | | | |

## Language lines

Each non-English line should be confirmed by a fluent reader before the run. Expected fixes:

| Line | Planted mistakes (Fix errors may also fix others, such as a missing comma) |
| --- | --- |
| en | recieved → received, " ,so" → ", so", lets → let's, tomorow → tomorrow |
| he | לפעמיים → לפעמים, " ,ואז" → ", ואז" (also holds an English word and a number) |
| ar | المشرووع → المشروع, " ,ثم" → "، ثم" (also holds an English word and a number) |
| ru | прочетал → прочитал, " ,поэтому" → ", поэтому", "вчера но" → "вчера, но" |
| es | recivimos → recibimos, a leído → ha leído, " ,así" → ", así" |
| fr | recu → reçu, " ,donc" → ", donc" |

Confirmed by: _name, date_
