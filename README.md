# Visk

- You have a cursor (head) position on the grid
- Each character typed is not stored in an input buffer. It is placed into the world as a new body segment
- The newly typed character is appended spatially relative to the body, typing grows your trail
- Because of that, the command you are typing is also the geometry you are creating

## Example

Initial:
```
>
```

Type p:
```
p>
```

Type u:
```
pu>
```

Then type another p:
```
pup^
```

Type r:

```
   ^
pupr
```

Then type i, resulting in:

```
   ^
   i
pupr
```

Eventually you will have something like:

```
   wow_this_is_cool_downn
   t                    i
   h                    c
   g                    e
   i                    v
pupr
```

## Concepts

- Commands are active abilities
- Protocols are passive modifiers
- Shells define starting loadouts

Runs consist of an endless amount of stages. Not every stage spawns an extract. 

## Enemies

### CHASER

A persistent hunter that tracks your coordinates across the grid.

### VIRUS

Infects your trail of characters, draining the Bytes you’ve collected for extraction.

### BLINDER

Corrupts your visual feed, forcing you to type and execute commands by feel for a temporary duration.

### FUSE

 Similar to a Chaser, but triggers a catastrophic explosion when it nears your trail.

## Commands

### Basic Commands

- Movement
  - `up`
  - `down`
  - `left`
  - `right`
- Coloring (to pass through color barriers)
  - `red`
  - `green`
  - `blue`

### ZAP

Deletes the closest enemy instantly.

### BOMB

Sets an explosive with a short fuse.

### MINE

Deploys a proximity trap for trailing enemies.

### SILENCE

Pauses all enemy movement for 20 steps.

### PING

Shows the direction to the thing that was pinged. What is pingable depends on the protocols the player has. By default only the `extract` is pingable via `ping_extract`.

### DASH

Teleports the player by pressing the TAB key.

## Protocols

### persistence

Coloring is retained for a longer duration before returning back to the standard input text color.

### gzip

Commands receive alternate short forms

| Command | Short Form |
| ------- | ---------- |
| down    | dn         |
| up      | up         |
| left    | lt         |
| right   | rt         |

### tab_completion

Allows to use tab to complete partially filled command pickups. The amount of characters able to be skipped depend on the level of tab_completion.

### backspace_buffer

Over keystrokes the backspace_buffer slowly filles to a max cap, allowing to delete without advancing in time.

### preview

Potential command completions are previewed slighlty in front the player path

### soft_wrap

When your trail would collide with itself at the head, it bends into the nearest legal adjacent tile instead of failing.

### route_lint

Changes the player cursor to alert how many characters are left before there is no option to turn away from the upcomming wall. Turns visibly red when this is the last possible moment to start typing the direction.

### rollback

The first backspace after completing a valid command removes the whole command token instead of one character.

## Map Elements

### ssh

`ssh_<stage_name>.stage`
`ssh_<shopkeeper_name>.store`

Allows to navigate out of the stage.

### firewall

The firewall scans the player trail and checks if everything up to a certain length is a valid command or valid english word (from a dictionary). Everything that is not valid will be counted. Firewalls show their percentage you need to have in order to pass them. The guard either bytes, commands or upgrades.

### bytes

Bytes are the currency in the game. Players build 1 byte for each character they type. Apart from that they can collect bytes on the map.

Bytes are colored `$` symbols and come in a variety of values.

| Color       | Value |
| ----------- | ----- |
| Light Blue  | 5     |
| Light Green | 10    |
| Light Red   | 50    |
| Golden      | 100   |
| Dark Purple | 500   |