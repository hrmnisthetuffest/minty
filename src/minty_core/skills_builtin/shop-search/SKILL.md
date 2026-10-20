---
name: shop-search
description: Find, price-check and shortlist products on shopping sites such as Amazon and Flipkart. Use when the user wants to find, look for, shop for, or get the cheapest or best product, for example "find me a desk lamp under 1500" or "what's a good phone charger".
allowed-tools: browser_task display_show
metadata:
  keywords: shop shopping buy find cheapest cheap best under price product amazon flipkart review rating deal
---
# Shop search

Do the browsing with `browser_task`; it runs in the background, so tell the user you are on it and keep talking.

1. Only ask a question if you cannot tell what product they mean. A budget, brand or colour goes into the goal.
2. Call `browser_task` with a complete goal, for example:
   "On amazon.in, search for 'LED desk lamp'. Open the top 3 relevant results. For each, note the name, price, rating
   and delivery date. Do not add anything to the cart. Report which one is the best value and why, in two sentences."
   Use the site the user named; otherwise amazon.in.
3. When the result comes back, say the pick, its price and why. If there are several good options, show them with
   `display_show` as a short table.
4. Never place an order or fill in payment details. Adding to the cart is fine only if the user asks for it.
