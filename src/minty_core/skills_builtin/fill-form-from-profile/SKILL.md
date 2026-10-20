---
name: fill-form-from-profile
description: Fill in a web form (name, address, phone, e-mail) using the details Minty remembers about the user, and stop before submitting. Use when the user asks to fill in, complete or enter their details on a form or checkout page.
allowed-tools: browser_task
metadata:
  keywords: fill form details address phone email name checkout registration signup enter complete profile
---
# Fill a form from remembered details

1. Use only facts in "Things you remember about the user" or that the user said in this conversation. If a field the
   form needs is not there, ask for it first; never guess.
2. Call `browser_task` with the exact values in the goal, for example: "On the open page, fill in the delivery form:
   name Sam Taylor, address 12 Park Road, city Pune, phone 9876543210. Do not press the final submit or place-order
   button; stop when the fields are filled and tell me what is left."
3. Never include passwords, card numbers, one-time codes or ID numbers. If the form asks for them, say the user
   should type those in the open window themselves.
4. After it is filled, read back what was entered and ask whether to submit. Submitting is a separate step and the
   browser will ask for confirmation before pressing it.
