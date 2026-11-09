# Three-minute owner walkthrough

Record in your own voice using the synthetic pack and the public-safe case study.
Use the clean repo and a terminal with readable type. Hide account details,
local absolute paths, notifications and private artifacts before capture.

| Time | Screen | Suggested narration |
|---|---|---|
| 0:00–0:20 | PDF page 1 | “I chose Find the Promos. An editor needs a start and an end, and a strong scene can make a weak promo if the hook arrives late or the ending gives away the answer.” |
| 0:20–0:48 | Synthetic timed input and terminal | “Each line has source times. Kuku Promo Lab proposes nearby continuous cuts and checks them against those exact times. Here is the model health check and the run command.” |
| 0:48–1:30 | Local synthetic report and two alternatives | “These cuts come from the same scene. Look at the first complete line and the last beat. A later start can move the threat into the opening. An earlier end can preserve the choice.” Show the real emitted timestamps and verdicts from the saved synthetic result. |
| 1:30–2:00 | Opener packet, ending evidence, CSV | “The opener check sees only the allowed opening slice. The ending check reads the cut and tests the proposed question. The export shows its evidence, flags and human review status.” |
| 2:00–2:35 | PDF page 2 | “The original run covered all 50 episodes. One result called a vague one-word reply a strong hook. Another treated a decision as unanswered after it had been made. These are the cases that motivated the separate checks.” |
| 2:35–2:55 | Validation summary and worksheet header | “The validator checks source coordinates and quotes. I would give these suggestions to an editor. The baseline comparison still needs human judgments.” |

Before recording, run the synthetic example or replay its saved model responses
and pick two actual alternative rows to discuss. Say “saved-response replay”
on camera if using replay and show that it made zero fresh calls. For a fresh
run, record the run command and show the completed result after an explicit
cut in the screen recording. Verify the upload opens without your own login.

## Gate-3 addition
Use the bundled synthetic replay fixtures only. Expand boundary_decision and
read the actual deciding factor aloud. Show the first synthetic family's
tie-break-only summary and the second family's different weighted judgments.
Point out that only single-edge variants were searched. The new V2 gate-3
fixture may produce no recommendations; show the review queue and explain
abstention. State that replay uses saved model responses and no new inference.
