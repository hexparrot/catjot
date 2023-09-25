# cat|jot

* Simple CLI note-taker centering around the present working directory.
* Notes can be easily created and manipulated with a shorthand syntax.
* Tags and Context include for greater organization.
* Output format is readily customizable.
* `cat`-centric, cat-themed

## Usage

### Cat-preferred Method for Note Creation:

Pipe `|` directly to catjot to create a new note.

```
$ cat|jot
 /\_/\     # cat directly to a new note (recommended).
( o.o )    # type each line exactly as you wish.
 > ^ <     # to save/exit note, move to beginning of newline and hit:
           # <CTRL-D>
$ cowsay -f kitty "meow" |jot
```

The notes are saved and can be recalled like this:

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

### Other Available Syntax for Note Creation

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

You can add context and tagging after-the-fact, with the -a (amend) toggle.
Amending always touches the last-written note in the file, no exception.

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

$ jot -at "sadnotes"
$ jot l
^-^
> cd /home/user
# date 2023-09-23 00:41:14 (1695454874)
[sadnotes]
% no more coffee after 8!
oh man, what am i even doing with my life!?
```

## Installation Steps:

Copy `catjot.py` to a directory found within your `$PATH`.
This document recommends and demonstrates examples that rename
this python script as `jot`, for ease-of-typing (length/autocomplete).

Popular destinations for saving the script include:
`$HOME/.local/bin` or `/usr/local/bin`.

### installation only within `$HOME` (single-user)

```
$ mkdir -p $HOME/.local/bin
$ chmod +x catjot.py
$ cp catjot.py $HOME/.local/bin/jot
```

### installation for system-wide use

```
# chmod +x catjot.py
# cp catjot.py /usr/local/bin/jot
```

In all cases, individual users' notes will appear in `~/.catjot`.

## Command Line Modifications

### Note Manipulation

`jot` : display all notes created in the present working directory (pwd)

`jot -ac "some info"` : Add context to last-written note
`|jot -ac`            : Piped content written as context for last-written note

`jot -ap "/var/log"` : Change pwd of last-written note
`|jot -ap`           : Piped content (single string, no spaces)
                     : written as pwd for last-written note

`jot -at "strays"`       : Add an additional tag to the last-written note
`|jot -at`               : Piped content (single string, no spaces)
                         : written as tag for last-written note
`jot -at -- "-inventory" : Subtract a tag matching a word preceded by a hyphen `-`
                         : The double `--` is to ensure the word is not interpreted
                         : by the argument parser, e.g., -a

### Homenotes

Some notes have very little connection to the path they are written in, and this is where
homenotes acts as a catch-all. Catjot uses a shortcut for homenotes to help facilitate
easy saving and recalling for path-agnostic notes:

```
$ cat|jot h
うち
$ jot h
^-^
> cd /home/user
# date 2023-09-20 07:36:31 (1695220591)
うち
```

### Shortcuts

`catjoy.py` defines many shortcuts to meet the syntax: `jot <letter>` for various functions.

`jot l`        : show last-written note
`jot p`        : pop/delete the last-written note
`jot d`        : show all notes from all time, everywhere
`jot pl`       : show last-written note, message PayLoad only (headers omitted)
`jot pl 169...`: show note matching timestamp(s), concatenated, message PayLoad only (headers omitted)
               : $ DATA=$(jot pl 1695220591)
               : $ echo $DATA
               : うち

### Alternate .catjot locations

Setting the environment variable `CATJOT_FILE` will allow you to choose a different location
other than `$HOME/.catjot`. The file directory and name can be set freely:

`export CATJOT_FILE=/home/user/mycatjot`

