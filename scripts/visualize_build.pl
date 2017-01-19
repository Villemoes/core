#!/usr/bin/perl
#

use strict;
use warnings;

use List::Util qw(min max);
use List::MoreUtils qw(any);
use Getopt::Long;

my %short_type = ( machine          => 'm',
		   native           => 'n',
		   cross            => 'c',
		   sdk              => 's',
		   'sdk-cross'      => 'S',
		   'canadian-cross' => 'C');

my @common_tasks = qw(stage fstage fetch unpack patch configure
compile install chrpath split package build);


my $max_task_width = max(map {length} @common_tasks);
my $max_recipe_width = 30 - $max_task_width - length("X Y:: ");
my $frame_width = 180; # assumed to be even
my $half_frame_width = int($frame_width/2);

my $tick = 1.0;
my $min_time = 0.5;
my @ignore = qw(fstage build);
my @ignore_opt;
my $print_legend = 1;

GetOptions("tick=f" => \$tick,
	   "min-time=f" => \$min_time,
	   "ignore=s" => \@ignore_opt,
	   "legend!" => \$print_legend,
    ) or die "Option error";
# --ignore somenoneexistingtaskname can be used to effectively ignore
# nothing (one may want to spell it --ignore none).

@ignore = @ignore_opt if @ignore_opt;
@ignore = split(/,/, join(',', @ignore));
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
	$type = $short_type{$type} // '?';
	$task =~ s/^do_//;
	next if any { $_ eq $task } @ignore;
	next if ($stop - $start) < $min_time;
	$recipe = abbrev($recipe, $max_recipe_width);
	$task = abbrev($task, $max_task_width);
	my $name = "${type}:${recipe}:${task}"; # All that? Hmm...
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
    return sort {$a->{time} <=> $b->{time}} @events;
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
    my $start = shift;
    for (my $lvl = 0; $lvl < @levels; ++$lvl) {
	return $lvl if $levels[$lvl] < $start;
    }
    return scalar @levels;
}
sub get_frame {
    my ($fi) = @_;
    my $frame = $frames[$fi];
    if (not defined $frame) {
	$frame = $frames[$fi] =
	{ left => $fi*$frame_width,
	  right => ($fi+1)*$frame_width,
	  tasks => [],
	  pixels => [],
	  legend => [],
	};
    }
    return $frame;
}

for my $event (@events) {
    my $task = $event->{task};
    if ($event->{type} eq 'start') {
	my $symbol = get_symbol();
	$task->{symbol} = $symbol;
	my $lvl = get_level($task->{start});
	$task->{level} = $lvl;
	$levels[$lvl] = $task->{stop};

	for (my $fi = frame_idx($task->{first}); $fi <= frame_idx($task->{last}); ++$fi) {
	    my $frame = get_frame($fi);
	    push @{$frame->{tasks}}, $task;
	    $frame->{pixels}[$lvl] //= ' 'x$frame_width;
	    my $l = max($frame->{left}, $task->{first});
	    # ->last is inclusive, ->right and r are exclusive
	    my $r = min($frame->{right}, $task->{last}+1);
	    substr($frame->{pixels}[$lvl], $l-$frame->{left}, $r-$l) =
		$task->{symbol} x ($r-$l);
	}
	# $levels[$lvl][$_] = $symbol for ($task->{first}..$task->{last});
	$current{$task->{id}} = $task;
	printf "%s\t%s\t%.2f\t%.2f\t%d\t%d\n", $symbol, $task->{name}, $task->{start}, $task->{stop},
$task->{first}, $task->{last};
    } elsif ($event->{type} eq 'stop') {
	my $id = $task->{id};
	die "task $id not in current" unless exists $current{$id};
	delete $current{$id};
	put_symbol($task->{symbol});
    } else {
	die "bad event";
    }
}

for (my $fi = 0; $fi < @frames; ++$fi) {
    my $frame = get_frame($fi);
    # Compute the 'legend'. Sort the tasks primarily by the position
    # of their leftmost symbol (in this frame), secondarily by their
    # level.
    $frame->{tasks} = [sort { max($frame->{left}, $a->{first}) <=> max($frame->{left}, $b->{first}) ||
				  $a->{level} <=> $b->{level} }
		       @{$frame->{tasks}}];
    # Figure out how many columns we have room for in this frame.
    my $max_name = max(map {length($_->{name})} @{$frame->{tasks}}) + 3;
    my $columns = int($frame_width / $max_name);
    # And how many rows do we then need?
    my $rows = int((scalar @{$frame->{tasks}} + $columns - 1)/$columns);
    $frame->{legend} = [(' 'x$frame_width) x $rows];
    # Write the names column-wise; with the above ordering of tasks,
    # that should make the legend for a symbol appear closeish to
    # where it appears in the "graph".
    for (my $n = 0; $n < @{$frame->{tasks}}; ++$n) {
	my $task = $frame->{tasks}[$n];
	my $i = $n % $rows;
	my $j = int($n / $rows);
	my $s = $task->{symbol} . ' ' . $task->{name};
	substr($frame->{legend}[$i], $j*$max_name, length($s)) = $s;
    }
}

# Make sure every graph and every legend has the same height.
my $max_graph_height = max(map { scalar @{$_->{pixels}} } @frames);
my $max_legend_height = max(map { scalar @{$_->{legend}} } @frames);
for my $frame (@frames) {
    for (my $lvl = 0; $lvl < $max_graph_height; ++$lvl) {
	$frame->{pixels}[$lvl] //= ' 'x$frame_width;
    }
    for (my $i = 0; $i < $max_legend_height; ++$i) {
	$frame->{legend}[$i] //= ' 'x$frame_width;
    }
}


for my $f (@frames) {
    my $header = ' 'x$frame_width;
    $header = insert_number($header, $f->{left}, 0);
    $header = insert_number($header, $f->{right}, $frame_width);
    $header = insert_number($header, int(($f->{left}+$f->{right})/2), $half_frame_width);
    print "$header\n";
    print "+" . ('-' x ($half_frame_width-1)) . "+" . ('-' x ($half_frame_width-1)) . "+\n";
    for my $line (@{$f->{pixels}}) {
	print "$line\n";
    }
    print "\n";
    next unless $print_legend;
    for my $line (@{$f->{legend}}) {
	print "$line\n";
    }
    print "\n";
}
# for my $lvl (0..$#levels) {
#     for my $i (0..$#{$levels[$lvl]}) {
# 	print ($levels[$lvl][$i] // ' ');
#     }
#     print "\n";
# }

sub abbrev {
    my ($s, $max) = @_;
    substr($s, $max-3) = '...' if length($s) > $max;
    return $s;
}
sub frame_idx {
    my ($i) = @_;
    return int($i/$frame_width);
}
sub insert_number {
    # Try to insert the number $d in $s such that the middle digit lands around $pos.
    my ($s, $d, $pos) = @_;
    $d = sprintf "%d", $d;
    my $w = length($d);
    $pos -= int($w/2);
    $pos = 0 if $pos < 0;
    substr($s, $pos, $w) = $d;
    return $s;
}
