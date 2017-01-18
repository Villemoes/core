#!/usr/bin/perl
#

use strict;
use warnings;

my @tasks = qw(configure stage install);
my @recipes = (1..10);
my @types = qw(m);

for my $t (@types) {
    for my $r (@recipes) {
	for my $task (@tasks) {
	    my $start = rand(50);
	    my $stop = $start + rand(10); # maybe make 10 depending on $task
	    printf "%s\t%f\t%f\n", "${t}:${r}:${task}", $start, $stop;
	}
    }
}
