# Instant answers

Some requests need no thinking: the time, a sum, a conversion, a dice roll, the capital of a country. Minty answers these
**by itself, the moment you finish speaking**, with no model call, no network and no quota used. The same goes for a few
spoken commands (a timer, the volume, pausing the music), which run as one tool call without a model in between.

- A request is answered this way only when **all of it** is one of the questions below. Anything longer, vaguer or different
  goes to the model as usual, so "what is the time complexity of quicksort" is never answered with the clock.
- Try any phrase without speaking: `minty instant "what is 15 percent of 240"`. List everything: `minty instant`.
- Turn it off with `instant.enabled = false`, the commands only with `instant.actions = false`, or leave a whole category to the
  model with `instant.skip = ["fun and games"]` (names below). Changes apply at the next request.
- This page is written from the code (`python -m minty_core.instant.cli --write`), and a test fails if it is out of date.

There are 43 groups and 388+ example phrases below; each group understands many more wordings than the examples.

## Categories

- **chat**: greeting, thanks, how are you, who are you, what can you do, are you there, version, never mind, repeat
- **time and date**: time now, date now, day now, month and year, weekend, other days, days until, clock maths, world time, calendar
- **this computer**: this computer, what is playing
- **maths**: percentages, one-step maths, whole numbers, statistics, number systems, shapes, arithmetic
- **conversions**: unit conversion
- **facts**: capitals, chemistry, full forms, constants and counts
- **fun and games**: chance, choose for me, jokes, facts and quotes, counting and spelling
- **do it for me** (commands): set a timer, how long is left on the timer, set a reminder, volume, screen, music controls

## chat

**greeting**  
“hello” · “good morning” · “good afternoon” · “good evening” · “good night” · “hi there” · “howdy”

**thanks**  
“thanks” · “thank you so much” · “thanks a lot” · “cheers” · “sorry” · “good job” · “well done” · “you are awesome” · “i love you”

**how are you**  
“how are you doing today” · “how is it going” · “what's up” · “how's your day going”

**who are you**  
“who are you” · “what's your name” · “introduce yourself” · “who made you” · “are you a robot” · “who created you” · “are you real” · “do you speak english”

**what can you do**  
“what can you do” · “what can I ask you” · “what are your features” · “help”

**are you there**  
“are you there” · “can you hear me” · “testing one two three” · “mic check” · “are you listening”

**version**  
“what version are you” · “which version are you running” · “what's your version”

**never mind**  
“never mind” · “forget it” · “forget that” · “scratch that”

**repeat**  
“say that again” · “repeat that” · “what did you say” · “what did I just ask” · “come again”


## time and date

**time now**  
“what time is it” · “what's the time” · “tell me the time” · “current time” · “time please” · “got the time” · “what's the time right now” · “do you have the time” · “what time is it now”

**date now**  
“what's the date” · “what's today's date” · “what date is it” · “today's date” · “what is the date today”

**day now**  
“what day is it” · “what day is today” · “which day is it” · “what's today” · “what day of the week is it”

**month and year**  
“what month is it” · “what year is it” · “what's the year” · “which month is it” · “current month” · “what week is it” · “what's the week number”

**weekend**  
“is it the weekend” · “is it a weekday” · “is today the weekend”

**other days**  
“what day is tomorrow” · “what's the date tomorrow” · “what was yesterday” · “what day was yesterday” · “what's tomorrow's date” · “what day is it in 10 days” · “what date is it in two weeks” · “what day was it 3 days ago” · “when is next friday” · “what date is monday” · “when is christmas” · “what day is christmas” · “what day of the week is halloween” · “what's yesterday's date”

**days until**  
“how many days until christmas” · “how many days till new year” · “days until halloween” · “how many days until the weekend” · “how many days until friday” · “how many days until 15 august” · “how many days left in the year” · “how many days are left this month” · “how many days since 1 january 2020” · “how many days until december 31” · “how many weeks until christmas”

**clock maths**  
“what time will it be in 2 hours” · “what time will it be in 45 minutes” · “what time was it 3 hours ago” · “how long until 6 pm” · “how many hours until 5” · “how many minutes until noon” · “how long till midnight” · “what time will it be in an hour and a half”

**world time**  
“what time is it in tokyo” · “time in london” · “what's the time in new york” · “what time is it in india” · “dubai time” · “what time is it in utc” · “time in sydney” · “what's the time in los angeles right now” · “what is my time zone”

**calendar**  
“is 2028 a leap year” · “is this a leap year” · “how many days in february” · “how many days are in march” · “how many days in a year” · “how many days are in this month” · “how many days in february 2028”


## this computer

**this computer**  
“how much battery do I have” · “battery level” · “is the battery charging” · “how much ram do I have” · “memory usage” · “how much disk space is left” · “how much storage do I have left” · “cpu usage” · “how busy is the computer” · “how many cpu cores do I have” · “what cpu do I have” · “how hot is the cpu” · “how long has the computer been on” · “uptime” · “what is my ip address” · “what's my computer name” · “who am I logged in as” · “what operating system am I using” · “what kernel version am I running” · “how long have you been running”

**what is playing**  
“what song is this” · “what's playing” · “who sings this” · “what is currently playing” · “what am I listening to”


## maths

**percentages**  
“what's 15 percent of 240” · “20% of 150” · “what percent of 80 is 20” · “30 is what percent of 120” · “increase 200 by 10 percent” · “reduce 80 by 25 percent” · “20 percent off 1500” · “15 percent tip on 840” · “18 percent gst on 1000” · “split 1200 between 4 people” · “add 10 percent to 50” · “subtract 20 percent from 400” · “what is 5 percent of two hundred”

**one-step maths**  
“square root of 144” · “cube root of 27” · “5 factorial” · “absolute value of -7” · “round 3.14159 to 2 decimals” · “round up 3.2” · “half of 50” · “double 16” · “a third of 90” · “log of 100” · “natural log of 10” · “sine of 30 degrees” · “what is pi” · “round 1234 to the nearest hundred” · “triple 7” · “log base 2 of 8” · “the golden ratio”

**whole numbers**  
“is 17 prime” · “factors of 36” · “prime factors of 84” · “gcd of 12 and 18” · “lcm of 4 and 6” · “is 12 even” · “greatest common divisor of 48 and 36” · “is 91 a prime number”

**statistics**  
“average of 4, 8 and 15” · “sum of 3 5 7” · “the maximum of 4 9 2” · “median of 1, 5, 9” · “product of 2, 3 and 4” · “range of 3 8 20”

**number systems**  
“255 in binary” · “convert 10 to binary” · “hex of 255” · “binary 1010 to decimal” · “roman numeral for 1994” · “what is XIV in numbers” · “7 times table” · “octal of 64” · “hexadecimal ff to decimal”

**shapes**  
“area of a circle with radius 7” · “circumference of a circle with diameter 10” · “area of a rectangle 5 by 8” · “perimeter of a rectangle 5 by 8” · “area of a triangle base 6 height 4” · “volume of a sphere with radius 3” · “volume of a cube with side 4” · “hypotenuse of 3 and 4” · “volume of a cylinder with radius 2 and height 5” · “area of a square with side 6”

**arithmetic**  
“what is 12 plus 7” · “144 divided by 12” · “what's 9 times 8” · “twelve minus five” · “2 to the power of 10” · “7 squared” · “what is (2 + 3) * 4” · “100 / 8” · “15 mod 4” · “5+5” · “multiply 6 by 7” · “add 5 and 3” · “what is 3 cubed” · “10 minus 4 times 2” · “calculate 99 times 99”


## conversions

**unit conversion**  
“convert 5 miles to kilometres” · “how many km in 5 miles” · “100 fahrenheit in celsius” · “30 c to f” · “what is 10 kg in pounds” · “how many ounces in a pound” · “how many cups in a gallon” · “5 feet in centimetres” · “how many seconds in a day” · “how many minutes in 3 hours” · “convert 60 mph to km/h” · “2 litres in millilitres” · “how many teaspoons in a tablespoon” · “1 gigabyte in megabytes” · “how many megabytes in a gigabyte” · “180 pounds in kilos” · “convert 6 feet to metres” · “how many square feet in an acre” · “50 knots in kilometres per hour” · “how many hours in a week” · “0 kelvin in celsius” · “what is 72 degrees fahrenheit in celsius” · “how many inches in a foot” · “how many grams in an ounce” · “convert 3 pints to litres” · “how many millilitres in a cup” · “twenty five celsius to fahrenheit”


## facts

**capitals**  
“what is the capital of france” · “capital of japan” · “what's the capital of india” · “which city is the capital of australia” · “what is paris the capital of” · “what's the capital of the usa” · “capital of south africa”

**chemistry**  
“what is the chemical symbol for gold” · “atomic number of oxygen” · “what element has the symbol Fe” · “what is the atomic mass of carbon” · “what is the formula for water” · “what is H2O” · “element 26” · “what does Na stand for in chemistry” · “what is the atomic number of helium”

**full forms**  
“what is the full form of cpu” · “ram full form” · “what does usb stand for” · “what is gps short for” · “expand api” · “what is the full form of upi”

**constants and counts**  
“how many continents are there” · “how many planets are there” · “what is the largest planet” · “what is the speed of light” · “what is the boiling point of water” · “what is absolute zero” · “how many bones are in the human body” · “how many players are in a cricket team” · “how many zeros in a crore” · “how many lakh in a crore” · “how many sides does a hexagon have” · “what day comes after friday” · “what month comes before march” · “how many letters in necessary” · “what is the tallest mountain” · “how far is the moon” · “how many states in india” · “what is the freezing point of water” · “how old is the universe” · “how many degrees in a circle”


## fun and games

**chance**  
“flip a coin” · “toss a coin” · “heads or tails” · “roll a die” · “roll two dice” · “roll a d20” · “roll 2d6” · “roll the dice” · “roll a 20 sided die” · “pick a number between 1 and 10” · “random number between 5 and 50” · “give me a random number” · “pick a number” · “pick a card” · “pick a random letter” · “give me a random colour” · “yes or no” · “ask the magic 8 ball” · “shake the magic eight ball” · “rock paper scissors” · “number between 1 and 6”

**choose for me**  
“pick between pizza and burgers” · “choose between red, blue or green” · “decide between tea or coffee” · “pick one of sleep, study or games”

**jokes, facts and quotes**  
“tell me a joke” · “another joke” · “make me laugh” · “say something funny” · “tell me a fun fact” · “random fact” · “did you know” · “surprise me” · “give me a quote” · “motivate me” · “inspire me” · “compliment me” · “say something nice” · “cheer me up” · “tell me a tongue twister” · “got any jokes”

**counting and spelling**  
“say the alphabet” · “count to 10” · “count down from 5” · “how do you spell necessary” · “spell restaurant” · “recite the alphabet” · “count up to twenty”


## do it for me (commands)

These become one tool call, checked by the same safety rules as anything the model asks for. If the tool is not set up (for example Spotify is not connected), the request goes to the model instead.

**set a timer**  
“set a timer for 5 minutes” · “timer for 90 seconds” · “start a 10 minute timer” · “set a pasta timer for twelve minutes” · “5 minute timer” · “set a timer for an hour and a half” · “set a timer for half an hour” · “set an alarm in 20 minutes”

**how long is left on the timer**  
“how much time is left” · “how long is left on the timer” · “any timers running” · “list my timers”

**set a reminder**  
“remind me in 10 minutes to call mom” · “remind me to check the oven in 20 minutes” · “remind me at 6 pm to take medicine” · “remind me to stretch at 4:30 pm” · “remind me in an hour to drink water”

**volume**  
“volume up” · “louder” · “turn the volume down” · “quieter” · “set the volume to 40” · “volume 60 percent” · “mute the sound” · “unmute the speakers” · “turn it up” · “volume to max”

**screen**  
“set the brightness to 50” · “brightness 70 percent” · “lock the screen” · “lock my laptop”

**music controls**  
“pause” · “pause the music” · “resume the music” · “unpause” · “next song” · “skip this song” · “previous song” · “skip” · “go back a song” · “resume”
