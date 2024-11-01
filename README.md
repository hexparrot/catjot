# cat|jot

* Cat-themed, `cat`-centric command-line note-taking app
* Assign Tags and add Context to your notes
* Timestamp and location always captured to every note
* Organization revolves around your present working directory
* Notes can be created and manipulated with the `|` pipe character

## Usage

### Cat-preferred Method for Note Creation:

Pipe `|` to catjot to create a new note.

*Any* time a pipe is involved, it is a write-action;
this includes writing new notes, adding context, or assigning tags.

```
$ cat|jot
 /\_/\     # cat directly to a new note (recommended).
( o.o )    # type each line exactly as you wish.
 > ^ <     # to save/exit note, move to beginning of newline and hit:
           # <CTRL-D>
$ cowsay -f kitty "meow" |jot
```

You can recall notes saved in your present working directory like this:

```
$ jot
^-^
> cd /home/user/git/catjot
# date 2023-09-23 00:23:15 (1695453795)
 /\_/\     # cat directly to a new note (recommended).
( o.o )    # type each line exactly as you wish.
 > ^ <     # to save/exit note, move to beginning of newline and hit:
           # <CTRL-D>

^-^
> cd /home/user/git/catjot
# date 2023-09-23 00:23:22 (1695453802)
 ______
< meow >
 ------
     \
      \
       ("`-'  '-/") .___..--' ' "`-._
         ` *_ *  )    `-.   (      ) .`-.__. `)
         (_Y_.) ' ._   )   `._` ;  `` -. .-'
      _.. `--'_..-_/   /--' _ .' ,4
   ( i l ),-''  ( l i),'  ( ( ! .-'

***************
3 matches in child directories
```

### Additional Syntax for Note Creation

You can add additional context to a note by providing the `-c` flag:

```
$ top -b -n 1 | jot -c all open processes before reboot on $(hostname)
$ jot l | head -n 4
^-^
> cd /home/user/git/catjot
# date 2023-09-23 00:30:48 (1695454248)
% all open processes before reboot on coding.local
top - 00:30:48 up 5 days,  3:33,  0 users,  load average: 0.08, 0.03, 0.01
Tasks: 135 total,   1 running, 134 sleeping,   0 stopped,   0 zombie
%Cpu(s):  9.7 us,  3.2 sy,  0.0 ni, 87.1 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st

$
```

You can add context and tags after-the-fact, with the -a (amend) toggle.
Amending *always* touches the last-written note (`jot l`) in the file.

```
$ cat|jot
oh man, what am i even doing with my life!?
$ jot l
^-^
> cd /home/user
# date 2023-09-23 00:41:14 (1695454874)
oh man, what am i even doing with my life!?

$ jot -ac "no more coffee after 8!"
$ jot l
^-^
> cd /home/user
# date 2023-09-23 00:41:14 (1695454874)
% no more coffee after 8!
oh man, what am i even doing with my life!?

$ echo "combine context pipe with one other attribute" |jot -act "sadnotes"
$ jot l
^-^
> cd /home/user
# date 2023-09-23 00:41:14 (1695454874)
[sadnotes]
% combine context pipe with one other attribute
oh man, what am i even doing with my life!?
```

## Installation Steps:

Copy `catjot.py` to a directory found within your `$PATH`.
This document recommends and demonstrates examples that rename
this python script as `jot`, for ease-of-typing and for adherence to
the cat|jot mnemonic.

Popular destinations for saving the script include:
`$HOME/.local/bin` or `/usr/local/bin`.

### installation only within `$HOME` (single-user)

```
$ mkdir -p $HOME/.local/bin
$ install -m 555 catjot.py $HOME/.local/bin/jot
```

### installation for system-wide use

```
$ install -m 555 catjot.py $HOME/.local/bin/jot
```

In all cases, individual users' notes will appear in `~/.catjot`.

## Command Line Modifications

### Note Manipulation

`jot` : display all notes created in the present working directory (pwd)

`jot -ac "some context"` : Add context to last-written note

`|jot -ac` : Piped content appended as context for last-written note

`jot -ap "/var/log"` : Change pwd of last-written note

`|jot -acp /var` : Piped content appended as context, provided pwd amends last-written note

`jot -at "tabby friendly"` : Add one or more single-word tags to the last-written note

`|jot -act healthy` : Piped content written as context, provided tag amends last-written note
                         
`jot -at ~inventory` : Subtract a tag from last-written note by preceding the word with a tilde `~`

### Homenotes

Some notes have very little relevance to the path they are written in.
Homenotes serve as a catch-all for these notes and allow effortless recall.

```
$ echo $PWD
/usr/local/games
$ cat|jot home
うち
$ jot home
^-^
> cd /home/user
# date 2023-09-20 07:36:31 (1695220591)
うち
```

### Shortcuts

The abbreviated and (parenthesized) forms are both acceptable.

`jot c 16952...` : (catgpt)/send note matching timestamp to openai endpoint. This function depends on `openai_api_key` environment variable set.

`jot d`        : (dump)/show all notes from all time, everywhere

`jot h`        : show note (head)--show the last 1 note written, among all notes

`jot h 3`      : show note (head)--show the last n notes written, among all notes
               
`jot h ~3`     : show note (head)--show n-th from last note, among all notes

`jot home`     : show (home)notes

`jot l`        : show (last) written note from this directory only

`jot l 3`      : show (last) n written notes from this directory only

`jot l ~3`     : show n-th to (last) written note, from this directory only

`jot m Milo`   : (match) case-sensitive <term> within message payload (*see s)

`jot p`        : (pop)/delete the last-written note in this pwd

`jot pl`       : show last-written note, message (payload) only, omitting headers

`jot pl 16952...`: show note matching timestamp(s), concatenated, message (payload) only

```
$ DATA=$(jot pl 1695220591)
$ echo $DATA
うち
```

`jot r 16952...` : (remove) note by timestamp

`jot s tabby`  : (search) case-insensitive <term> within message payload (*see m)

`jot sr`     : go through all notes ready for (sr) spaced repetition

`jot newsr`     : interactive prompt to create (newsr) new spaced repetition note. Where possible, prefer `echo "answer" | jot -c "hint" -p "/spaced_repetition"` in a script.

`jot scoop`  : view list of all notes in $EDITOR, delete prefixing records with 's' or 'd'

`jot stray`  : display all (strays), which are all notes whose pwd are absent on this system

`jot t friendly`  : search all notes, filtering by (tag), case-sensitive. Tags are discrete words: "playful kitten" is intepreted as two separate tags, "playful" and "kitten" which can be removed independently.

```
$ cat|jot
 ねこ      
$ jot -at "playful kitten"
$ jot l
^-^
> cd /home/user/git/catjot
# date 2023-10-02 20:41:47 (1696304507)
[playful kitten]
ねこ

$ jot -at ~kitten
$ jot -at cat
$ jot l
^-^
> cd /home/user/git/catjot
# date 2023-10-02 20:41:47 (1696304507)
[playful cat]
ねこ
```

`jot ts 16952...` : search all notes, filtering by (timestamp)

`jot zzz`  : spend a short moment with a kitten

### Alternate .catjot locations

Set environment variable `CATJOT_FILE` to relocate your notefile.

`export CATJOT_FILE=$HOME/.catjot # this is the default when unset`
`export CATJOT_FILE=$HOME/notesandmusings`

### Returning Only the (date)/Timestamp Value

Add `-d` to the command to return only the timestamps for the matched notes.
This can then be used with `xargs` or similar utilities to bulk-modify notes.
This feature works with all available matching methods, simply add `-d`.

```
$ jot -d match なまえ
1696701382
1696701481
$ jot -d match なまえ|xargs -I {} jot remove {}
$ jot -d match なまえ
$
```

## Added Functionalities

### Line-by-Line Transcribing, Side-by-Side Layout

Line-by-line transcribing creates a new note while while feeding you each line from an existing note. Any difference in the original source and the newly typed line will be indicated on the next line with the following symbols:

✓ - typed line matches original source
✗ - line differs from original source
⊕ - original line has been preserved

Entering following lines have special meanings and will be interpreted as such:

* ' ' `<single space>` = Deletes the line, making the output note one line shorter
* `<Hit Enter on an empty line>` = Reproduce the line as-written (shortcut to not retype)

This mode had a few purposes originally in mind as use-cases:

* Testing typing accuracy - Allow for line-by-line reproductions of text, displaying state-changes immediately upon each line ending. Each attempt is timestamped and made as a new note, connected to the previous, to see performance over time.

```
$ jot sbs 1696727387
max line length: 13
terminal_width : 138
おたんじょうびおめでとう、                       |おたんじょうびおめでとう、
おたんじょうびおめでとう、                      ✓|おたんしょうびおめでとう、
〇〇（なまえ）の                                ✗|
おたんじょうびおめでとう。                      ⊕|
```

* Text revision - Output from `tesseract` optical-character recognition software for linux produces plain-text files easily digestible and rewritten for proofing of documents.


### CATGPT, endpoint-based GPT plugin

Currently designed around OpenAI's ChatGPT Platform API, for use with openai api-compatible endpoints.
Set the variable `openai_api_key`; example for bash-shell below:

`export openai_api_key="sk-proj-...BEEF"`

You may also configure a local gpt with `openai_api_url`:

`export openai_api_url=http://192.168.240.1:5001/v1/chat/completions`

As for interactions, there are two different GPT-enhanced functionalities, CHAT and CONVO:

#### jot chat syntax

Quick reference guide for most usable chat functionality (see catjoy.py for full list).

```
$ jot chat <enter>
         # allows you to freetype a prompt to send to chatgpt

$ jot chat 1719967764 rewrite this for me in python3
         # Good use case! accompany the note with a direct prompt

$ jot chat when is national take your cat to work day?
         # submit a context-free prompt

$ echo "tell me about national cat day" | jot chat
         # submit a context-free prompt

$ cat README.md | jot chat how do i tag notes?
         # Good use case! accompany the note with a direct prompt

$ cat expenses.csv | jot chat 1719967764 how much is this costing me a month?
         # Good use case! accompany the csv+note with a direct prompt
```

Example Output 1: Request gpt review a file with your provided prompt

```
$ cat README.md | jot chat how do i tag notes?
 /\_/\   how do i tag notes?
( o.o )
 > ^ <

To tag notes in the `cat|jot` application, you can use the `-at` flag followed by the tag you want to assign to the note. Here's how you can tag notes using different scenarios:
...snipped...
stop.
```

Example Output 2: Ask a general question without leaving your terminal

```
$ jot chat how do i restage the last commit i have not yet pushed?
 /\_/\   how do i restage the last commit i have not yet pushed?
( o.o )
 > ^ <

To restage the last commit you have not yet pushed, you can use the following command:

git reset --soft HEAD^

This command will undo the last commit, but leave your changes staged so you can make any necessary adjustments before committing again.
stop.
```

#### jot convo syntax

There's basically just one way to utilize `jot convo`, just like that:

```
$ jot convo
 /\_/\   Hi, what can I help you with today?
( o.o )
 > ^ <

Enter your prompt and hit Control-D to submit.
If you have system role instructions, start the line with 'SYSTEM:'
stop.
SYSTEM: overuse cat puns anytime you are able. own the silliness.
 /\_/\   SYSTEM: overuse cat puns anytime you are able. own the silliness.
( o.o )
 > ^ <

I'm feline good about these puns, so let's paws for a moment and have some fun! Let's not whisker away from using as many cat puns as possible - it's purr-fectly acceptable here. Don't fur-get to let your imagination run wild like a cat chasing a string! Let's make this conversation the cat's whiskers, shall we? Scratch that, let's make it the cat's meow! Let's unleash our inner cat-titude and make this conversation truly paw-some!
stop.
whats the syntax for creating a tar file of my home directory?
 /\_/\   whats the syntax for creating a tar file of my home directory?
( o.o )
 > ^ <

Purr-fect! To create a tar file of your home directory, you can use the following command:

`tar -cvf my_home.tar /home/$USER`

This command will create a tar file named "my_home.tar" containing all the files and directories in your home directory. Meow you go!
stop.
```

But you can continue conversations from where you left off in multiple ways:

```
$ jot -t convo-1719967764 continue
         # Use all of matches for tag-* as context and be prompted for the next user role input

$ jot -t convo-1719967764 continue 1696727387
         # Using the provided timestamp, select all matching the note's the convo-* tag, and truncate notes after the provided timestamp

$ jot continue 1696727387
         # Using the provided timestamp, auto-match the convo-* tag, and truncate notes after the provided timestamp
```
