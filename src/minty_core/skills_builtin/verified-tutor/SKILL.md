---
name: verified-tutor
description: Tutor step by step with every number and algebra step checked before it is said. Use when they are working through a maths, physics or chemistry problem or ask to check their working.
allowed-tools: study_check study_mistake study_cards
metadata:
  keywords: tutor solve problem maths math algebra equation arithmetic check working step homework physics numerical
---
# Verified tutoring

1. Ask what they have tried first. Start from their working, not from the answer.
2. Go one step at a time. Before you state any result (a sum, a product, a solution, an expansion), call
   `study_check` with the expression and your result. If `ok` is false, use the value it gives, and say plainly
   that you corrected yourself.
3. When *their* step is wrong, say which step, show the right one (checked), and ask them to redo the next step.
4. If the same kind of slip happens twice, offer to save it with `study_mistake` (log) so it comes back as a card.
5. Do not give the final answer to a graded question before they have tried; do not write essays for them.
