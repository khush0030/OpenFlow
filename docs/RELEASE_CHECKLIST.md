# Release checklist

Run this before every `./scripts/build_app.sh --deploy` that ships a user-facing
change, and tick it in the PR's test plan. About 10 minutes. A change counts as
done only when it's been seen working on screen (ROADMAP › Principles).

## Before building

- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`: all green
- [ ] Changed hub pages rendered with `scripts/hub_shots.py` at 985×760 and
      1280×832; nothing clips
- [ ] `git status` clean apart from the change being shipped

## After deploying

Check the app relaunched cleanly:

- [ ] `pgrep -fl OpenFlow.app`: one menu bar process plus `flow-widget`, no
      Python processes, no OpenFlow item in the Dock
- [ ] `tail ~/.openflow/errors.log`: nothing new since the deploy

### Dictation

- [ ] Hold the key in **VS Code chat**: text lands, the paste sound plays, no card
- [ ] Same in a **Chrome** text box and in **Slack**
- [ ] A **long take** (30 s+) gets sensible paragraphs; a spoken list
      ("number one… number two…") becomes a list
- [ ] **Hands-free**: double-tap, talk, tap to finish; the text lands
- [ ] A **quick tap** does nothing; **⌥+arrow** in an editor doesn't start a take
- [ ] **Hinglish**: a mixed sentence comes out as English

### Cancel and recovery

- [ ] Hold, release, press **Esc** at once: nothing pastes, cancel sound plays
- [ ] Same with the widget's **✕**
- [ ] **Undo last paste** (⌘⇧Z) removes the last dictation
- [ ] Click into no text field and dictate: the copy card appears with the text
- [ ] Once the Phase 4 failover and retry work ships: **Wi-Fi off**, dictate,
      see "Saved · Retry"; Wi-Fi on, press Retry, the text lands

### Edit and command mode

- [ ] Select text, ⌘⇧E, say "make this shorter": the selection is rewritten
- [ ] In a reply box with nothing selected, ⌘⇧E, say "reply saying yes but
      push to Friday": a reply appears at the cursor

### The OpenFlow window

- [ ] Opens from the menu bar and the Dock with the OpenFlow icon; closing it
      leaves the widget running
- [ ] Home, Insights (every tab), History, Dictionary, Tone & language,
      Settings, Help: nothing clipped at your usual window size

## Afterwards

- [ ] PR test plan ticked; anything that failed is filed or fixed before merging
