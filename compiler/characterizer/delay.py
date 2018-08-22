import sys,re,shutil
import debug
import tech
import math
from .stimuli import *
from .trim_spice import *
from .charutils import *
import utils
from globals import OPTS

class delay():
    """Functions to measure the delay and power of an SRAM at a given address and
    data bit.

    In general, this will perform the following actions:
    1) Trim the netlist to remove unnecessary logic.
    2) Find a feasible clock period using max load/slew on the trimmed netlist.
    3) Characterize all loads/slews and consider fail when delay is greater than 5% of feasible delay using trimmed netlist.
    4) Measure the leakage during the last cycle of the trimmed netlist when there is no operation.
    5) Measure the leakage of the whole netlist (untrimmed) in each corner.
    6) Subtract the trimmed leakage and add the untrimmed leakage to the power.

    Netlist trimming can be removed by setting OPTS.trim_netlist to
    False, but this is VERY slow.

    """

    def __init__(self, sram, spfile, corner):
        self.sram = sram
        self.name = sram.name
        self.word_size = self.sram.word_size
        self.addr_size = self.sram.addr_size
        self.num_cols = self.sram.num_cols
        self.num_rows = self.sram.num_rows
        self.num_banks = self.sram.num_banks
        self.sp_file = spfile

        # These are the member variables for a simulation
        self.period = 0
        self.set_load_slew(0,0)
        self.set_corner(corner)

    def set_corner(self,corner):
        """ Set the corner values """
        self.corner = corner
        (self.process, self.vdd_voltage, self.temperature) = corner
        
    def set_load_slew(self,load,slew):
        """ Set the load and slew """
        self.load = load
        self.slew = slew
        
    def check_arguments(self):
        """Checks if arguments given for write_stimulus() meets requirements"""
        try:
            int(self.probe_address, 2)
        except ValueError:
            debug.error("Probe Address is not of binary form: {0}".format(self.probe_address),1)

        if len(self.probe_address) != self.addr_size:
            debug.error("Probe Address's number of bits does not correspond to given SRAM",1)

        if not isinstance(self.probe_data, int) or self.probe_data>self.word_size or self.probe_data<0:
            debug.error("Given probe_data is not an integer to specify a data bit",1)

    def write_generic_stimulus(self):
        """ Create the instance, supplies, loads, and access transistors. """

        # add vdd/gnd statements
        self.sf.write("\n* Global Power Supplies\n")
        self.stim.write_supply()

        # instantiate the sram
        self.sf.write("\n* Instantiation of the SRAM\n")
        self.stim.inst_sram(abits=self.addr_size, 
                            dbits=self.word_size, 
                            sram_name=self.name)

        self.sf.write("\n* SRAM output loads\n")
        for i in range(self.word_size):
            self.sf.write("CD{0} DOUT[{0}] 0 {1}f\n".format(i,self.load))
        

    def write_delay_stimulus(self):
        """ Creates a stimulus file for simulations to probe a bitcell at a given clock period.
        Address and bit were previously set with set_probe().
        Input slew (in ns) and output capacitive load (in fF) are required for charaterization.
        """
        self.check_arguments()

        # obtains list of time-points for each rising clk edge
        self.create_test_cycles()

        # creates and opens stimulus file for writing
        temp_stim = "{0}/stim.sp".format(OPTS.openram_temp)
        self.sf = open(temp_stim, "w")
        self.sf.write("* Delay stimulus for period of {0}n load={1}fF slew={2}ns\n\n".format(self.period,
                                                                                             self.load,
                                                                                             self.slew))
        self.stim = stimuli(self.sf, self.corner)
        # include files in stimulus file
        self.stim.write_include(self.trim_sp_file)

        self.write_generic_stimulus()
        
        # generate data and addr signals
        self.sf.write("\n* Generation of data and address signals\n")
        self.gen_data()
        self.gen_addr()


        # generate control signals
        self.sf.write("\n* Generation of control signals\n")
        self.gen_control()

        self.sf.write("\n* Generation of global clock signal\n")
        self.stim.gen_pulse(sig_name="CLK",
                            v1=0,
                            v2=self.vdd_voltage,
                            offset=self.period,
                            period=self.period,
                            t_rise=self.slew,
                            t_fall=self.slew)
                          
        self.write_delay_measures()

        # run until the end of the cycle time
        self.stim.write_control(self.cycle_times[-1] + self.period)

        self.sf.close()


    def write_power_stimulus(self, trim):
        """ Creates a stimulus file to measure leakage power only. 
        This works on the *untrimmed netlist*.
        """
        self.check_arguments()

        # obtains list of time-points for each rising clk edge
        self.create_test_cycles()

        # creates and opens stimulus file for writing
        temp_stim = "{0}/stim.sp".format(OPTS.openram_temp)
        self.sf = open(temp_stim, "w")
        self.sf.write("* Power stimulus for period of {0}n\n\n".format(self.period))
        self.stim = stimuli(self.sf, self.corner)
        
        # include UNTRIMMED files in stimulus file
        if trim:
            self.stim.write_include(self.trim_sp_file)
        else:
            self.stim.write_include(self.sim_sp_file)
            
        self.write_generic_stimulus()
        
        # generate data and addr signals
        self.sf.write("\n* Generation of data and address signals\n")
        for i in range(self.word_size):
            self.stim.gen_constant(sig_name="DIN[{0}]".format(i),
                                   v_val=0)
        for i in range(self.addr_size):
            self.stim.gen_constant(sig_name="A[{0}]".format(i),
                                   v_val=0)

        # generate control signals
        self.sf.write("\n* Generation of control signals\n")
        self.stim.gen_constant(sig_name="CSb", v_val=self.vdd_voltage)
        self.stim.gen_constant(sig_name="WEb", v_val=self.vdd_voltage)
        self.stim.gen_constant(sig_name="OEb", v_val=self.vdd_voltage)        

        self.sf.write("\n* Generation of global clock signal\n")
        self.stim.gen_constant(sig_name="CLK", v_val=0)  
                          
        self.write_power_measures()

        # run until the end of the cycle time
        self.stim.write_control(2*self.period)

        self.sf.close()
        
    def write_delay_measures(self):
        """
        Write the measure statements to quantify the delay and power results.
        """

        self.sf.write("\n* Measure statements for delay and power\n")

        # Output some comments to aid where cycles start and
        # what is happening
        for comment in self.cycle_comments:
            self.sf.write("* {}\n".format(comment))

        # Trigger on the clk of the appropriate cycle
        trig_name = "clk"
        targ_name = "{0}".format("DOUT[{0}]".format(self.probe_data))
        trig_val = targ_val = 0.5 * self.vdd_voltage

        # Delay the target to measure after the negative edge
        self.stim.gen_meas_delay(meas_name="DELAY_HL",
                                 trig_name=trig_name,
                                 targ_name=targ_name,
                                 trig_val=trig_val,
                                 targ_val=targ_val,
                                 trig_dir="RISE",
                                 targ_dir="FALL",
                                 trig_td=self.cycle_times[self.read0_cycle],
                                 targ_td=self.cycle_times[self.read0_cycle])

        self.stim.gen_meas_delay(meas_name="DELAY_LH",
                                 trig_name=trig_name,
                                 targ_name=targ_name,
                                 trig_val=trig_val,
                                 targ_val=targ_val,
                                 trig_dir="RISE",
                                 targ_dir="RISE",
                                 trig_td=self.cycle_times[self.read1_cycle],
                                 targ_td=self.cycle_times[self.read1_cycle])

        self.stim.gen_meas_delay(meas_name="SLEW_HL",
                                 trig_name=targ_name,
                                 targ_name=targ_name,
                                 trig_val=0.9*self.vdd_voltage,
                                 targ_val=0.1*self.vdd_voltage,
                                 trig_dir="FALL",
                                 targ_dir="FALL",
                                 trig_td=self.cycle_times[self.read0_cycle],
                                 targ_td=self.cycle_times[self.read0_cycle])

        self.stim.gen_meas_delay(meas_name="SLEW_LH",
                                 trig_name=targ_name,
                                 targ_name=targ_name,
                                 trig_val=0.1*self.vdd_voltage,
                                 targ_val=0.9*self.vdd_voltage,
                                 trig_dir="RISE",
                                 targ_dir="RISE",
                                 trig_td=self.cycle_times[self.read1_cycle],
                                 targ_td=self.cycle_times[self.read1_cycle])
        
        # add measure statements for power
        t_initial = self.cycle_times[self.write0_cycle]
        t_final = self.cycle_times[self.write0_cycle+1]
        self.stim.gen_meas_power(meas_name="WRITE0_POWER",
                                 t_initial=t_initial,
                                 t_final=t_final)

        t_initial = self.cycle_times[self.write1_cycle]
        t_final = self.cycle_times[self.write1_cycle+1]
        self.stim.gen_meas_power(meas_name="WRITE1_POWER",
                                 t_initial=t_initial,
                                 t_final=t_final)
        
        t_initial = self.cycle_times[self.read0_cycle]
        t_final = self.cycle_times[self.read0_cycle+1]
        self.stim.gen_meas_power(meas_name="READ0_POWER",
                                 t_initial=t_initial,
                                 t_final=t_final)

        t_initial = self.cycle_times[self.read1_cycle]
        t_final = self.cycle_times[self.read1_cycle+1]
        self.stim.gen_meas_power(meas_name="READ1_POWER",
                                 t_initial=t_initial,
                                 t_final=t_final)

        
    def write_power_measures(self):
        """
        Write the measure statements to quantify the leakage power only. 
        """

        self.sf.write("\n* Measure statements for idle leakage power\n")

        # add measure statements for power
        t_initial = self.period
        t_final = 2*self.period
        self.stim.gen_meas_power(meas_name="leakage_power",
                                 t_initial=t_initial,
                                 t_final=t_final)
        
    def find_feasible_period(self):
        """
        Uses an initial period and finds a feasible period before we
        run the binary search algorithm to find min period. We check if
        the given clock period is valid and if it's not, we continue to
        double the period until we find a valid period to use as a
        starting point. 
        """

        feasible_period = float(tech.spice["feasible_period"])
        #feasible_period = float(2.5)#What happens if feasible starting point is wrong?
        time_out = 8
        while True:
            debug.info(1, "Trying feasible period: {0}ns".format(feasible_period))
            time_out -= 1

            if (time_out <= 0):
                debug.error("Timed out, could not find a feasible period.",2)
            self.period = feasible_period
            (success, results)=self.run_delay_simulation()
            if not success:
                feasible_period = 2 * feasible_period
                continue
            feasible_delay_lh = results["delay_lh"]
            feasible_slew_lh = results["slew_lh"]
            feasible_delay_hl = results["delay_hl"]
            feasible_slew_hl = results["slew_hl"]

            delay_str = "feasible_delay {0:.4f}ns/{1:.4f}ns".format(feasible_delay_lh, feasible_delay_hl)
            slew_str = "slew {0:.4f}ns/{1:.4f}ns".format(feasible_slew_lh, feasible_slew_hl)
            debug.info(1, "Found feasible_period: {0}ns {1} {2} ".format(feasible_period,
                                                                         delay_str,
                                                                         slew_str))
            self.period = feasible_period
            return (feasible_delay_lh, feasible_delay_hl)


    def run_delay_simulation(self):
        """
        This tries to simulate a period and checks if the result works. If
        so, it returns True and the delays, slews, and powers.  It
        works on the trimmed netlist by default, so powers do not
        include leakage of all cells.
        """

        # Checking from not data_value to data_value
        self.write_delay_stimulus()

        self.stim.run_sim()
        delay_hl = parse_spice_list("timing", "delay_hl")
        delay_lh = parse_spice_list("timing", "delay_lh")
        slew_hl = parse_spice_list("timing", "slew_hl")
        slew_lh = parse_spice_list("timing", "slew_lh")
        delays = (delay_hl, delay_lh, slew_hl, slew_lh)

        read0_power=parse_spice_list("timing", "read0_power")
        write0_power=parse_spice_list("timing", "write0_power")
        read1_power=parse_spice_list("timing", "read1_power")
        write1_power=parse_spice_list("timing", "write1_power")

        if not self.check_valid_delays(delays):
            return (False,{})
            
        # For debug, you sometimes want to inspect each simulation.
        #key=raw_input("press return to continue")

        # Scale results to ns and mw, respectively
        result = { "delay_hl" : delay_hl*1e9,
                   "delay_lh" : delay_lh*1e9,
                   "slew_hl" : slew_hl*1e9,
                   "slew_lh" : slew_lh*1e9,
                   "read0_power" : read0_power*1e3,
                   "read1_power" : read1_power*1e3,
                   "write0_power" : write0_power*1e3,
                   "write1_power" : write1_power*1e3}
            
        # The delay is from the negative edge for our SRAM
        return (True,result)


    def run_power_simulation(self):
        """ 
        This simulates a disabled SRAM to get the leakage power when it is off.
        
        """

        self.write_power_stimulus(trim=False)
        self.stim.run_sim()
        leakage_power=parse_spice_list("timing", "leakage_power")
        debug.check(leakage_power!="Failed","Could not measure leakage power.")


        self.write_power_stimulus(trim=True)
        self.stim.run_sim()
        trim_leakage_power=parse_spice_list("timing", "leakage_power")
        debug.check(trim_leakage_power!="Failed","Could not measure leakage power.")

        # For debug, you sometimes want to inspect each simulation.
        #key=raw_input("press return to continue")
        return (leakage_power*1e3, trim_leakage_power*1e3)
    
    def check_valid_delays(self, delay_tuple):
        """ Check if the measurements are defined and if they are valid. """

        (delay_hl, delay_lh, slew_hl, slew_lh) = delay_tuple
        period_load_slew_str = "period {0} load {1} slew {2}".format(self.period,self.load, self.slew)
        
        # if it failed or the read was longer than a period
        if type(delay_hl)!=float or type(delay_lh)!=float or type(slew_lh)!=float or type(slew_hl)!=float:
            delays_str = "delay_hl={0} delay_lh={1}".format(delay_hl, delay_lh)
            slews_str = "slew_hl={0} slew_lh={1}".format(slew_hl,slew_lh)
            debug.info(2,"Failed simulation (in sec):\n\t\t{0}\n\t\t{1}\n\t\t{2}".format(period_load_slew_str,
                                                                                         delays_str,
                                                                                         slews_str))
            return False
        # Scale delays to ns (they previously could have not been floats)
        delay_hl *= 1e9
        delay_lh *= 1e9
        slew_hl *= 1e9
        slew_lh *= 1e9
        delays_str = "delay_hl={0} delay_lh={1}".format(delay_hl, delay_lh)
        slews_str = "slew_hl={0} slew_lh={1}".format(slew_hl,slew_lh)
        if delay_hl>self.period or delay_lh>self.period or slew_hl>self.period or slew_lh>self.period:
            debug.info(2,"UNsuccessful simulation (in ns):\n\t\t{0}\n\t\t{1}\n\t\t{2}".format(period_load_slew_str,
                                                                                              delays_str,
                                                                                              slews_str))
            return False
        else:
            debug.info(2,"Successful simulation (in ns):\n\t\t{0}\n\t\t{1}\n\t\t{2}".format(period_load_slew_str,
                                                                                            delays_str,
                                                                                            slews_str))

        return True
        

    def find_min_period(self, feasible_delay_lh, feasible_delay_hl):
        """
        Searches for the smallest period with output delays being within 5% of 
        long period. 
        """

        previous_period = ub_period = self.period
        lb_period = 0.0

        # Binary search algorithm to find the min period (max frequency) of design
        time_out = 25
        while True:
            time_out -= 1
            if (time_out <= 0):
                debug.error("Timed out, could not converge on minimum period.",2)

            target_period = 0.5 * (ub_period + lb_period)
            self.period = target_period
            debug.info(1, "MinPeriod Search: {0}ns (ub: {1} lb: {2})".format(target_period,
                                                                             ub_period,
                                                                             lb_period))

            if self.try_period(feasible_delay_lh, feasible_delay_hl):
                ub_period = target_period
            else:
                lb_period = target_period
                #debug.error("Lower bound "+str(target_period)+" caused a failed simulation.Exiting...",2)

            if relative_compare(ub_period, lb_period, error_tolerance=0.05):
                # ub_period is always feasible
                return ub_period


    def try_period(self, feasible_delay_lh, feasible_delay_hl):
        """ 
        This tries to simulate a period and checks if the result
        works. If it does and the delay is within 5% still, it returns True.
        """

        # Checking from not data_value to data_value
        self.write_delay_stimulus()
        self.stim.run_sim()
        delay_hl = parse_spice_list("timing", "delay_hl")
        delay_lh = parse_spice_list("timing", "delay_lh")
        slew_hl = parse_spice_list("timing", "slew_hl")
        slew_lh = parse_spice_list("timing", "slew_lh")
        # if it failed or the read was longer than a period
        if type(delay_hl)!=float or type(delay_lh)!=float or type(slew_lh)!=float or type(slew_hl)!=float:
            debug.info(2,"Invalid measures: Period {0}, delay_hl={1}ns, delay_lh={2}ns slew_hl={3}ns slew_lh={4}ns".format(self.period,
                                                                                                                           delay_hl,
                                                                                                                           delay_lh,
                                                                                                                           slew_hl,
                                                                                                                           slew_lh))
            return False
        delay_hl *= 1e9
        delay_lh *= 1e9
        slew_hl *= 1e9
        slew_lh *= 1e9
        if delay_hl>self.period or delay_lh>self.period or slew_hl>self.period or slew_lh>self.period:
            debug.info(2,"Too long delay/slew: Period {0}, delay_hl={1}ns, delay_lh={2}ns slew_hl={3}ns slew_lh={4}ns".format(self.period,
                                                                                                                              delay_hl,
                                                                                                                              delay_lh,
                                                                                                                              slew_hl,
                                                                                                                              slew_lh))
            return False
        else:
            if not relative_compare(delay_lh,feasible_delay_lh,error_tolerance=0.05):
                debug.info(2,"Delay too big {0} vs {1}".format(delay_lh,feasible_delay_lh))
                return False
            elif not relative_compare(delay_hl,feasible_delay_hl,error_tolerance=0.05):
                debug.info(2,"Delay too big {0} vs {1}".format(delay_hl,feasible_delay_hl))
                return False


        #key=raw_input("press return to continue")

        debug.info(2,"Successful period {0}, delay_hl={1}ns, delay_lh={2}ns slew_hl={3}ns slew_lh={4}ns".format(self.period,
                                                                                                                delay_hl,
                                                                                                                delay_lh,
                                                                                                                slew_hl,
                                                                                                                slew_lh))
        return True
    
    def set_probe(self,probe_address, probe_data):
        """ Probe address and data can be set separately to utilize other
        functions in this characterizer besides analyze."""
        self.probe_address = probe_address
        self.probe_data = probe_data

        self.prepare_netlist()
        

    def prepare_netlist(self):
        """ Prepare a trimmed netlist and regular netlist. """
        
        # Set up to trim the netlist here if that is enabled
        if OPTS.trim_netlist:
            self.trim_sp_file = "{}reduced.sp".format(OPTS.openram_temp)
            self.trimsp=trim_spice(self.sp_file, self.trim_sp_file)
            self.trimsp.set_configuration(self.num_banks,
                                          self.num_rows,
                                          self.num_cols,
                                          self.word_size)
            self.trimsp.trim(self.probe_address,self.probe_data)
        else:
            # The non-reduced netlist file when it is disabled
            self.trim_sp_file = "{}sram.sp".format(OPTS.openram_temp)
            
        # The non-reduced netlist file for power simulation 
        self.sim_sp_file = "{}sram.sp".format(OPTS.openram_temp)
        # Make a copy in temp for debugging
        shutil.copy(self.sp_file, self.sim_sp_file)



    def analyze(self,probe_address, probe_data, slews, loads):
        """
        Main function to characterize an SRAM for a table. Computes both delay and power characterization.
        """
        # Data structure for all the characterization values
        char_data = {}
        
        self.set_probe(probe_address, probe_data)

        # This is for debugging a full simulation
        # debug.info(0,"Debug simulation running...")
        # target_period=50.0
        # feasible_delay_lh=0.059083183
        # feasible_delay_hl=0.17953789
        # load=1.6728
        # slew=0.04
        # self.try_period(target_period, feasible_delay_lh, feasible_delay_hl)
        # sys.exit(1)


        # 1) Find a feasible period and it's corresponding delays using the trimmed array.
        self.load=max(loads)
        self.slew=max(slews)
        (feasible_delay_lh, feasible_delay_hl) = self.find_feasible_period()
        debug.check(feasible_delay_lh>0,"Negative delay may not be possible")
        debug.check(feasible_delay_hl>0,"Negative delay may not be possible")
        
        # 2) Finds the minimum period without degrading the delays by X%
        self.set_load_slew(max(loads),max(slews))
        min_period = self.find_min_period(feasible_delay_lh, feasible_delay_hl)
        debug.check(type(min_period)==float,"Couldn't find minimum period.")
        debug.info(1, "Min Period: {0}n with a delay of {1} / {2}".format(min_period, feasible_delay_lh, feasible_delay_hl))
        char_data["min_period"] = round_time(min_period)

        # Make a list for each type of measurement to append results to
        for m in ["delay_lh", "delay_hl", "slew_lh", "slew_hl", "read0_power",
                  "read1_power", "write0_power", "write1_power", "leakage_power"]:
            char_data[m]=[]

        # 3) Find the leakage power of the trimmmed and  UNtrimmed arrays.
        (full_array_leakage, trim_array_leakage)=self.run_power_simulation()
        char_data["leakage_power"]=full_array_leakage

        # 4) At the minimum period, measure the delay, slew and power for all slew/load pairs.
        for slew in slews:
            for load in loads:
                self.set_load_slew(load,slew)
                # Find the delay, dynamic power, and leakage power of the trimmed array.
                (success, delay_results) = self.run_delay_simulation()
                debug.check(success,"Couldn't run a simulation. slew={0} load={1}\n".format(self.slew,self.load))
                for k,v in delay_results.items():
                    if "power" in k:
                        # Subtract partial array leakage and add full array leakage for the power measures
                        char_data[k].append(v - trim_array_leakage + full_array_leakage)
                    else:
                        char_data[k].append(v)



        return char_data



    def add_data(self, data):
        """ Add the array of data values """
        debug.check(len(data)==self.word_size, "Invalid data word size.")
        index = 0
        for c in data:
            if c=="0":
                self.data_values[index].append(0)
            elif c=="1":
                self.data_values[index].append(1)
            else:
                debug.error("Non-binary data string",1)
            index += 1

    def add_address(self, address):
        """ Add the array of address values """
        debug.check(len(address)==self.addr_size, "Invalid address size.")
        index = 0
        for c in address:
            if c=="0":
                self.addr_values[index].append(0)
            elif c=="1":
                self.addr_values[index].append(1)
            else:
                debug.error("Non-binary address string",1)
            index += 1
    
    def add_noop(self, comment, address, data):
        """ Add the control values for a read cycle. """
        self.cycle_comments.append("Cycle {0:2d}\t{1:5.2f}ns:\t{2}".format(len(self.cycle_times),
                                                                           self.t_current,
                                                                           comment))
        self.cycle_times.append(self.t_current)
        self.t_current += self.period
        self.web_values.append(1)
        self.oeb_values.append(1)
        self.csb_values.append(1)

        self.add_data(data)
        self.add_address(address)
        
                 
    def add_read(self, comment, address, data):
        """ Add the control values for a read cycle. """
        self.cycle_comments.append("Cycle {0:2d}\t{1:5.2f}ns:\t{2}".format(len(self.cycle_comments),
                                                                           self.t_current,
                                                                           comment))
        self.cycle_times.append(self.t_current)
        self.t_current += self.period
        
        self.web_values.append(1)
        self.oeb_values.append(0)
        self.csb_values.append(0)

        self.add_data(data)
        self.add_address(address)
        
        

    def add_write(self, comment, address, data):
        """ Add the control values for a read cycle. """
        self.cycle_comments.append("Cycle {0:2d}\t{1:5.2f}ns:\t{2}".format(len(self.cycle_comments),
                                                                           self.t_current,
                                                                           comment))
        self.cycle_times.append(self.t_current)
        self.t_current += self.period
        
        self.web_values.append(0)
        self.oeb_values.append(1)
        self.csb_values.append(0)

        self.add_data(data)
        self.add_address(address)
        
    def create_test_cycles(self):
        """Returns a list of key time-points [ns] of the waveform (each rising edge)
        of the cycles to do a timing evaluation. The last time is the end of the simulation
        and does not need a rising edge."""

        # Start at time 0
        self.t_current = 0

        # Cycle times (positive edge) with comment
        self.cycle_comments = []
        self.cycle_times = []

        # Control logic signals each cycle
        self.web_values = []
        self.oeb_values = []
        self.csb_values = []

        # Address and data values for each address/data bit
        self.data_values=[]
        for i in range(self.word_size):
            self.data_values.append([])
        self.addr_values=[]
        for i in range(self.addr_size):
            self.addr_values.append([])

        # Create the inverse address for a scratch address
        inverse_address = ""
        for c in self.probe_address:
            if c=="0":
                inverse_address += "1"
            elif c=="1":
                inverse_address += "0"
            else:
                debug.error("Non-binary address string",1)

        # For now, ignore data patterns and write ones or zeros
        data_ones = "1"*self.word_size
        data_zeros = "0"*self.word_size
        
        self.add_noop("Idle cycle (no positive clock edge)",
                      inverse_address, data_zeros)

        self.add_write("W data 1 address 0..00",
                       inverse_address,data_ones) 

        self.add_write("W data 0 address 11..11 to write value",
                       self.probe_address,data_zeros)
        self.write0_cycle=len(self.cycle_times)-1 # Remember for power measure
        
        # This also ensures we will have a H->L transition on the next read
        self.add_read("R data 1 address 00..00 to set DOUT caps",
                      inverse_address,data_zeros) 

        self.add_read("R data 0 address 11..11 to check W0 worked",
                      self.probe_address,data_zeros) 
        self.read0_cycle=len(self.cycle_times)-1 # Remember for power measure
        
        self.add_noop("Idle cycle (if read takes >1 cycle)",
                      inverse_address,data_zeros)
        self.idle_cycle=len(self.cycle_times)-1 # Remember for power measure

        self.add_write("W data 1 address 11..11 to write value",
                       self.probe_address,data_ones)
        self.write1_cycle=len(self.cycle_times)-1 # Remember for power measure

        self.add_write("W data 0 address 00..00 to clear DIN caps",
                       inverse_address,data_zeros)

        # This also ensures we will have a L->H transition on the next read
        self.add_read("R data 0 address 00..00 to clear DOUT caps",
                      inverse_address,data_zeros)
        
        self.add_read("R data 1 address 11..11 to check W1 worked",
                      self.probe_address,data_zeros)
        self.read1_cycle=len(self.cycle_times)-1 # Remember for power measure

        self.add_noop("Idle cycle (if read takes >1 cycle))",
                      self.probe_address,data_zeros)



    def analytical_delay(self,sram, slews, loads):
        """ Just return the analytical model results for the SRAM. 
        """
        delay_lh = []
        delay_hl = []
        slew_lh = []
        slew_hl = []
        for slew in slews:
            for load in loads:
                self.set_load_slew(load,slew)
                bank_delay = sram.analytical_delay(self.slew,self.load)
                # Convert from ps to ns
                delay_lh.append(bank_delay.delay/1e3)
                delay_hl.append(bank_delay.delay/1e3)
                slew_lh.append(bank_delay.slew/1e3)
                slew_hl.append(bank_delay.slew/1e3)
        
        power = sram.analytical_power(self.process, self.vdd_voltage, self.temperature, load) 
        #convert from nW to mW
        power.dynamic /= 1e6 
        power.leakage /= 1e6
        debug.info(1,"Dynamic Power: {0} mW".format(power.dynamic))        
        debug.info(1,"Leakage Power: {0} mW".format(power.leakage)) 
        
        data = {"min_period": 0, 
                "delay_lh": delay_lh,
                "delay_hl": delay_hl,
                "slew_lh": slew_lh,
                "slew_hl": slew_hl,
                "read0_power": power.dynamic,
                "read1_power": power.dynamic,
                "write0_power": power.dynamic,
                "write1_power": power.dynamic,
                "leakage_power": power.leakage
                }
        return data

    def gen_data(self):
        """ Generates the PWL data inputs for a simulation timing test. """
        for i in range(self.word_size):
            sig_name="DIN[{0}]".format(i)
            self.stim.gen_pwl(sig_name, self.cycle_times, self.data_values[i], self.period, self.slew, 0.05)

    def gen_addr(self):
        """ 
        Generates the address inputs for a simulation timing test. 
        This alternates between all 1's and all 0's for the address.
        """
        for i in range(self.addr_size):
            sig_name = "A[{0}]".format(i)
            self.stim.gen_pwl(sig_name, self.cycle_times, self.addr_values[i], self.period, self.slew, 0.05)


    def gen_control(self):
        """ Generates the control signals """
        self.stim.gen_pwl("csb", self.cycle_times, self.csb_values, self.period, self.slew, 0.05)
        self.stim.gen_pwl("web", self.cycle_times, self.web_values, self.period, self.slew, 0.05)
        self.stim.gen_pwl("oeb", self.cycle_times, self.oeb_values, self.period, self.slew, 0.05)
