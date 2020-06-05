# See LICENSE for licensing information.
#
# Copyright (c) 2016-2019 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import contact
import pinv
import debug
from tech import drc, parameter
from vector import vector
from globals import OPTS
from sram_factory import factory

if(OPTS.tech_name == "s8"):
    from tech import nmos_bins, pmos_bins, accuracy_requirement

    
class pinv_dec(pinv.pinv):
    """
    This is another version of pinv but with layout for the decoder.
    Other stuff is the same (netlist, sizes, etc.)
    """

    def __init__(self, name, size=1, beta=parameter["beta"], height=None, add_wells=True):

        debug.info(2,
                   "creating pinv_dec structure {0} with size of {1}".format(name,
                                                                             size))
        if not height:
            b = factory.create(module_type="bitcell")
            self.cell_height = b.height
        else:
            self.cell_height = height

        # Inputs to cells are on input layer
        # Outputs from cells are on output layer
        if OPTS.tech_name == "s8":
            self.supply_layer = "m1"
        else:
            self.supply_layer = "m2"
        
        pinv.pinv.__init__(self, name, size, beta, self.cell_height, add_wells)

    def determine_tx_mults(self):
        """
        Determines the number of fingers needed to achieve the size within
        the height constraint. This may fail if the user has a tight height.
        """

        # This is always 1 tx, because we have horizontal transistors.
        self.tx_mults = 1
        self.nmos_width = self.nmos_size * drc("minwidth_tx")
        self.pmos_width = self.pmos_size * drc("minwidth_tx")
        if OPTS.tech_name == "s8":
            (self.nmos_width, self.tx_mults) = self.bin_width("nmos", self.nmos_width)
            (self.pmos_width, self.tx_mults) = self.bin_width("pmos", self.pmos_width)
            return
        
    # Over-ride the route input gate to call the horizontal version.
    # Other top-level netlist and layout functions are not changed.
    def route_input_gate(self, pmos_inst, nmos_inst, ypos, name, position="left", directions=None):
        """
        Route the input gate to the left side of the cell for access.
        Position is actually ignored and is left to be compatible with the pinv.
        """

        nmos_gate_pin = nmos_inst.get_pin("G")
        pmos_gate_pin = pmos_inst.get_pin("G")

        # Check if the gates are aligned and give an error if they aren't!
        if nmos_gate_pin.ll().y != pmos_gate_pin.ll().y:
            self.gds_write("unaliged_gates.gds")
        debug.check(nmos_gate_pin.ll().y == pmos_gate_pin.ll().y,
                    "Connecting unaligned gates not supported. See unaligned_gates.gds.")
        
        # Pick point on the left of NMOS and up to PMOS
        nmos_gate_pos = nmos_gate_pin.rc()
        pmos_gate_pos = pmos_gate_pin.lc()
        self.add_path("poly", [nmos_gate_pos, pmos_gate_pos])

        # Center is completely symmetric.
        contact_width = contact.poly_contact.width
        contact_offset = nmos_gate_pin.lc() \
                         - vector(self.poly_extend_active + 0.5 * contact_width, 0)
        via = self.add_via_stack_center(from_layer="poly",
                                        to_layer=self.route_layer,
                                        offset=contact_offset,
                                        directions=directions)
        self.add_path("poly", [contact_offset, nmos_gate_pin.lc()])
        
        self.add_layout_pin_rect_center(text=name,
                                        layer=self.route_layer,
                                        offset=contact_offset,
                                        width=via.mod.second_layer_width,
                                        height=via.mod.second_layer_height)

    def determine_width(self):
        self.width = self.pmos_inst.rx() + self.well_extend_active

    def extend_wells(self):
        """ Extend bottom to top for each well. """

        from tech import layer
        if "pwell" in layer:
            ll = self.nmos_inst.ll() - self.nmos_inst.mod.active_offset
            ur = self.nmos_inst.ur() + self.nmos_inst.mod.active_offset
            self.add_rect(layer="pwell",
                          offset=ll,
                          width=ur.x - ll.x,
                          height=self.height - ll.y)

        if "nwell" in layer:
            ll = self.pmos_inst.ll() - self.pmos_inst.mod.active_offset
            ur = self.pmos_inst.ur() + self.pmos_inst.mod.active_offset
            self.add_rect(layer="nwell",
                          offset=ll - vector(self.nwell_enclose_active, 0),
                          width=ur.x - ll.x + self.nwell_enclose_active,
                          height=self.height - ll.y + 2 * self.nwell_enclose_active)
            
    def place_ptx(self):
        """
        """

        # offset so that the input contact is over from the left edge by poly spacing
        x_offset = self.nmos.active_offset.y + contact.poly_contact.width + self.poly_space
        # center the transistor in the y-dimension
        y_offset = self.nmos.width + self.active_space
        self.nmos_pos = vector(x_offset, y_offset)
        self.nmos_inst.place(self.nmos_pos)
        self.nmos_inst.place(self.nmos_pos,
                             rotate=270)
        # place PMOS so it is half a poly spacing down from the top
        xoffset = self.nmos_inst.height + 2 * self.poly_extend_active + 2 * self.well_extend_active + drc("pwell_to_nwell")
        self.pmos_pos = self.nmos_pos + vector(xoffset, 0)
        self.pmos_inst.place(self.pmos_pos,
                             rotate=270)

        # Output position will be in between the PMOS and NMOS drains
        pmos_drain_pos = self.pmos_inst.get_pin("D").center()
        nmos_drain_pos = self.nmos_inst.get_pin("D").center()
        self.output_pos = vector(0.5 * (pmos_drain_pos.x + nmos_drain_pos.x), nmos_drain_pos.y)

    def route_outputs(self):
        """
        Route the output (drains) together.
        Optionally, routes output to edge.
        """
            
        # Get the drain pin
        nmos_drain_pin = self.nmos_inst.get_pin("D")

        # Pick point at right most of NMOS and connect over to PMOS
        nmos_drain_pos = nmos_drain_pin.lc()
        right_side = vector(self.width, nmos_drain_pos.y)

        self.add_layout_pin_segment_center("Z",
                                           self.route_layer,
                                           nmos_drain_pos,
                                           right_side)

    def add_well_contacts(self):
        """ Add n/p well taps to the layout and connect to supplies """

        source_pos = self.pmos_inst.get_pin("S").center()
        contact_pos = vector(source_pos.x, self.height)
        self.nwell_contact = self.add_via_center(layers=self.active_stack,
                                                 offset=contact_pos,
                                                 implant_type="n",
                                                 well_type="n")
        self.add_via_stack_center(offset=contact_pos,
                                  from_layer=self.active_stack[2],
                                  to_layer=self.supply_layer)

        source_pos = self.nmos_inst.get_pin("S").center()
        contact_pos = vector(source_pos.x, self.height)
        self.pwell_contact= self.add_via_center(layers=self.active_stack,
                                                offset=contact_pos,
                                                implant_type="p",
                                                well_type="p")
        self.add_via_stack_center(offset=contact_pos,
                                  from_layer=self.active_stack[2],
                                  to_layer=self.supply_layer)
        
    def route_supply_rails(self):
        pin = self.nmos_inst.get_pin("S")
        source_pos = pin.center()
        bottom_pos = source_pos.scale(1, 0)
        top_pos = bottom_pos + vector(0, self.height)
        self.add_layout_pin_segment_center("gnd",
                                           self.supply_layer,
                                           start=bottom_pos,
                                           end=top_pos)

        pin = self.pmos_inst.get_pin("S")
        source_pos = pin.center()
        bottom_pos = source_pos.scale(1, 0)
        top_pos = bottom_pos + vector(0, self.height)
        self.add_layout_pin_segment_center("vdd",
                                           self.supply_layer,
                                           start=bottom_pos,
                                           end=top_pos)
        
    def connect_rails(self):
        """ Connect the nmos and pmos to its respective power rails """

        source_pos = self.nmos_inst.get_pin("S").center()
        self.add_via_stack_center(offset=source_pos,
                                  from_layer=self.route_layer,
                                  to_layer=self.supply_layer)

        source_pos = self.pmos_inst.get_pin("S").center()
        self.add_via_stack_center(offset=source_pos,
                                  from_layer=self.route_layer,
                                  to_layer=self.supply_layer)
        
