# Visk

Concepts:

- Commands are active abilities
- Protocols are passive modifiers
- Shells define starting loadouts

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