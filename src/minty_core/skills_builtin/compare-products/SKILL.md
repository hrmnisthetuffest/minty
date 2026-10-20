---
name: compare-products
description: Compare two or more named products or options side by side on price, rating and key features. Use when the user asks which is better or cheaper, or to compare X and Y.
allowed-tools: browser_task display_show
metadata:
  keywords: compare comparison versus better cheaper difference differences which between option options
---
# Compare products

1. Make sure you have the options by name. If the user gave fewer than two, ask which ones.
2. Call `browser_task` with a goal that names every option, for example: "On amazon.in, look up the 'Philips LED
   desk lamp' and the 'Wipro LED desk lamp'. For each, note the price, rating, number of ratings and the two most
   useful features. Do not add anything to the cart. Say which is better value."
3. Show the comparison with `display_show` as a table (option, price, rating, key features) and say the verdict in
   one or two sentences, including any caveat such as few ratings.
4. If the numbers disagree between sources, say so instead of picking one silently.
