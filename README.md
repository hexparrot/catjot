# cat|jot

* Simple CLI note-taker centering around the present working directory.
* Notes can be easily created and manipulated with a shorthand syntax.
* Output format is readily customizable.

## Usage

### Cat-preferred Method for Note Creation:

Pipe `|` directly to catjot to create a new note.

```
$ cat|jot
 /\_/\     # cat directly to a new note (recommended).
( o.o )    # type each line exactly as you wish.
 > ^ <     # to save/exit note, move to beginning of newline and hit:
           # <CTRL-D>
$ cowsay -f kitty "meow"|jot
```

The notes are saved and can be recalled like this:

```
$ jot

> cd /home/user
# date 2023-09-17 19:01:10 (1695002470)
 /\_/\     # cat directly to a new note (recommended).
( o.o )    # type each line exactly as you wish.
 > ^ <     # to save/exit note, move to beginning of newline and hit:
           # <CTRL-D>

***************

> cd /home/user
# date 2023-09-17 19:01:25 (1695002485)
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

### Cat-acceptble Method for Note Creation

```
$ echo -e "mew\nmew\nmew" | jot    # also permssible
$ jot -a "foo"                     # save a single string argument
```

### Additional, Frivolous cat-permissible method:

```
$ jot << cat
> this is a frivolous, vanity use of cat, as en EOF marker
> cat
$ jot
cd /home/user
# date 2023-09-16 15:24:58 (1694903098)
this is a frivolous, vanity use of cat, as en EOF marker
```

## Installation Steps:

Copy `catjot.py` to a directory found within your `$PATH`.
This document recommends and demonstrates examples that rename
this python script as `jot`, for ease-of-typing (length/autocomplete).
This also helps encourage the mnemonic cat|jot for writing a quick note.

Popular destinations include `$HOME/.local/bin` or `/usr/local/bin`.

### installation only within `$HOME` (single-user)

```
$ mkdir -p $HOME/.local/bin
$ cp catjot.py $HOME/.local/bin/jot
```

### installation for system-wide use

```
# cp catjot.py /usr/local/bin/jot
```

In all cases, individual users' notes will appear in `~/.catjot`.

## Note Manipulation

`jot` : display all notes created in the present working directory (pwd)

`jot -s "<search term>"` : search *all* notes against simple string match (note field only)

`jot -a "<new note content>"` : append a new single-line note

`jot -d <timestamp>` : delete any notes matching (unix timestamp)

