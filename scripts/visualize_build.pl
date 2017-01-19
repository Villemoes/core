#!/usr/bin/perl
#

use strict;
use warnings;

use List::Util qw(min max);
use List::MoreUtils qw(any);

my %defaults = (
		tick => 1.0,
		min_time => 0.5,
		ignore => [qw(fstage build)],
	       );

my $tick;
my $min_time;
my @ignore;

# --ignore somenoneexistingtaskname can be used to effectively ignore
# nothing (one may want to spell it --ignore none).

$tick //= $defaults{tick};
$min_time //= $defaults{min_time};
@ignore = @{$defaults{ignore}} unless @ignore;
@ignore = map { s/^do_// } @ignore;

# Try not to recycle a symbol too soon. If we ever have more than 52
# simultaneous tasks, we'll use the same symbol for two things at the
# same time. Tough.
my @symbols = ();
sub get_symbol {
    @symbols = ('a'..'z', 'A'..'Z') unless @symbols;
    return shift @symbols;
}
sub put_symbol {
    push @symbols, shift;
}

sub read_data {
    my $file = shift;
    my %tasks;
    my $offset = 'inf';
    open(my $fh, '<', $file)
	or die "cannot open $file for reading: $!";
    while (<$fh>) {
	next if m/^#/;
	chomp;
	my ($id, $start, $stop, undef) = split;
	my ($type, $recipe, $task) = split /:/, $id;
	$task =~ s/^do_//;
	my $name = "${type}:${recipe}:${task}"; # All that? Hmm...
	next if any { $_ eq $task } @ignore;
	next if ($stop - $start) < $min_time;
	$offset = min($offset, $start, $stop);
	die "duplicate task $id" if exists $tasks{$id};
	$tasks{$id} = {id => $id, name => $name, start => $start, stop => $stop};
    }
    close($fh);
    for (values %tasks) {
	# Make all values 0-based, relative to the earliest known time.
	$_->{start} -= $offset;
	$_->{stop} -= $offset;
	# Measure time in "ticks".
	$_->{start} /= $tick;
	$_->{stop} /= $tick;
	# Compute the first and last "pixel". Pixel i, corresponding
	# to times [i, i+1], is included if the intersection of that
	# interval with [start, stop] has size >= .5. Except that we
	# want at least one pixel covered.
	my $i = int($_->{start});
	$_->{first} = ($_->{start} - $i <= .5) ? $i : $i+1;
	$i = int($_->{stop});
	$_->{last} = max($_->{first}, ($_->{stop} - $i >= .5) ? $i : $i-1);
#	printf "%s\t%f\t%f\t%d\t%d\n", $_->{id}, $_->{start}, $_->{stop}, $_->{first}, $_->{last};
    }
    return %tasks;
}

sub create_events {
    my $tasks = shift;
    my @events;
    for (values %$tasks) {
	push @events, {time => $_->{start}, type => 'start', task => $_};
	push @events, {time => $_->{stop}, type => 'stop', task => $_};
    }
    @events = sort {$a->{time} <=> $b->{time}} @events;
    return @events;
}

my %tasks = read_data($ARGV[0]);
my @events = create_events(\%tasks);
my @levels; # this should just track $max_time at that level
my %current;
my @frames; # the symbols should be written to the frames

# A task has an assigned symbol and a "level" (the height above the
# x-axis where its symbols will appear). It occupies the pixels for
# which its [start, stop] interval overlaps at least half the time
# that pixel represents.
sub get_level {
    my $first = shift;
    my $lvl = 0;
    $lvl++ while (defined $levels[$lvl][$first]);
    return $lvl;
}

for my $event (@events) {
    my $task = $event->{task};
    if ($event->{type} eq 'start') {
	my $symbol = get_symbol();
	$task->{symbol} = $symbol;
	my $lvl = get_level($task->{first});
	$task->{level} = $lvl;
	$levels[$lvl][$_] = $symbol for ($task->{first}..$task->{last});
	$current{$task->{id}} = $task;
	printf "%s\t%s\n", $symbol, $task->{name};
    } elsif ($event->{type} eq 'stop') {
	my $id = $task->{id};
	die "task $id not in current" unless exists $current{$id};
	delete $current{$id};
	put_symbol($task->{symbol});
    } else {
	die "bad event";
    }
}

for my $lvl (0..$#levels) {
    for my $i (0..$#{$levels[$lvl]}) {
	print ($levels[$lvl][$i] // ' ');
    }
    print "\n";
}
