'''
pyBlock

pyBlcok is a (fairly) complete volume geometry engine. It performs
multiple functions including fitting volumes and mesh warping volumes. 
The actual b-spline volumes are of the pySpline volume type. See the individual
functions for additional information

Copyright (c) 2010 by G. Kenway
All rights reserved. Not to be used for commercial purposes.
Revision: 1.0   $Date: 11/03/2010$


Developers:
-----------
- Gaetan Kenway (GKK)

History
-------
	v. 1.0 - Initial Class Creation (GKK, 2010)
'''

# =============================================================================
# Standard Python modules
# =============================================================================

import os, sys, copy, time

# =============================================================================
# External Python modules
# =============================================================================
import numpy

from numpy.linalg import lstsq,inv,norm

try:
    from scipy import sparse,io
    from scipy.sparse.linalg.dsolve import factorized
    from scipy.sparse.linalg import bicgstab,gmres
    USE_SCIPY_SPARSE = True
except:
    USE_SCIPY_SPARSE = False
    print 'There was an error importing scipy scparse tools'

# =============================================================================
# Extension modules
# =============================================================================
from mdo_import_helper import import_modules, mpiPrint, MPI
exec(import_modules('geo_utils','pySpline'))
import geo_utils, pySpline # not required, but pylint is happier

# =============================================================================
# pyBlock class
# =============================================================================
class pyBlock():
	
    def __init__(self,init_type,*args, **kwargs):
        
        '''Create an instance of the geometry object. The initialization type,
        init_type, specifies what type of initialization will be
        used. There are currently 4 initialization types: plot3d,
        iges, lifting_surface and acdt_geo

        
        Input: 
        
        init_type, string: a key word defining how this geo object
        will be defined. Valid Options/keyword argmuents are:

        'plot3d',file_name = 'file_name.xyz' : Load in a plot3D
        surface patches and use them to create splined volumes
        '''
        
        # First thing to do is to check if we want totally silent
        # operation i.e. no print statments
        if 'no_print' in kwargs:
            self.NO_PRINT = kwargs['no_print']
        else:
            self.NO_PRINT = False
        # end if
        self.init_type = init_type
        self.FFD = False

        mpiPrint(' ',self.NO_PRINT)
        mpiPrint('------------------------------------------------',self.NO_PRINT)
        mpiPrint('pyBlock Initialization Type is: %s'%(init_type),self.NO_PRINT)
        mpiPrint('------------------------------------------------',self.NO_PRINT)

        #------------------- pyVol Class Atributes -----------------
        self.topo = None         # The topology of the volumes/surface
        self.vols = []           # The list of volumes (pySpline volume)
        self.nVol = None         # The total number of volumessurfaces
        self.coef  = None        # The global (reduced) set of control pts
        self.embeded_volumes = {}
        # --------------------------------------------------------------

        if init_type == 'plot3d':
            self._readPlot3D(*args,**kwargs)
        elif init_type == 'cgns':
            self._readCGNS(*args,**kwargs)
        elif init_type == 'bvol':
            self._readBVol(*args,**kwargs)
        elif init_type == 'create':
            pass
        else:
            mpiPrint('init_type must be one of plot3d,cgns, or bvol.')
            sys.exit(0)
        return
# ----------------------------------------------------------------------
#                     Initialization Types
# ----------------------------------------------------------------------    

    def _readPlot3D(self,*args,**kwargs):

        '''Load a plot3D file and create the splines to go with each patch'''
        assert 'file_name' in kwargs,'file_name must be specified for plot3d'
        assert 'file_type' in kwargs,'file_type must be specified as binary or ascii'
        assert 'order'     in kwargs,'order must be specified as \'f\' or \'c\''
        file_name = kwargs['file_name']        
        file_type = kwargs['file_type']
        order     = kwargs['order']
        mpiPrint(' ',self.NO_PRINT)
        if file_type == 'ascii':
            mpiPrint('Loading ascii plot3D file: %s ...'%(file_name),self.NO_PRINT)
            binary = False
            f = open(file_name,'r')
        else:
            mpiPrint('Loading binary plot3D file: %s ...'%(file_name),self.NO_PRINT)
            binary = True
            f = open(file_name,'rb')
        # end if
        if binary:
            itype = geo_utils.readNValues(f,1,'int',binary)[0]
            nVol = geo_utils.readNValues(f,1,'int',binary)[0]
            itype = geo_utils.readNValues(f,1,'int',binary)[0] # Need these
            itype = geo_utils.readNValues(f,1,'int',binary)[0] # Need these
            sizes   = geo_utils.readNValues(f,nVol*3,'int',binary).reshape((nVol,3))
        else:
            nVol = geo_utils.readNValues(f,1,'int',binary)[0]
            sizes   = geo_utils.readNValues(f,nVol*3,'int',binary).reshape((nVol,3))
        # end if

        mpiPrint(' -> nVol = %d'%(nVol),self.NO_PRINT)

        blocks = []
        for i in xrange(nVol):
            cur_size = sizes[i,0]*sizes[i,1]*sizes[i,2]
            blocks.append(numpy.zeros([sizes[i,0],sizes[i,1],sizes[i,2],3]))
            for idim in xrange(3):
                blocks[-1][:,:,:,idim] = geo_utils.readNValues(
                    f,cur_size,'float',binary).reshape(
                    (sizes[i,0],sizes[i,1],sizes[i,2]),order=order)
            # end for
        # end for

        f.close()

        # Now create a list of spline volume objects:
        self.vols = []
        # Note This doesn't actually fit the volumes...just produces
        # the parameterization and knot vectors

        if 'FFD' in kwargs and kwargs['FFD']:
            self.FFD = True
                # Assemble blocks directly from the coefficients:

            def uniform_knots(N,k):
                knots = numpy.zeros(N+k)
                knots[0:k-1] = 0.0 
                knots[-k:] = 1.0
                knots[k-1:-k+1] = numpy.linspace(0,1,N-k+2)

                return knots

            for ivol in xrange(nVol):
                ku = min(4,sizes[ivol,0])
                kv = min(4,sizes[ivol,1])
                kw = min(4,sizes[ivol,2])

                # A unform knot vector is ok and we won't have to
                #propagate the vecotrs since they are by
                #construction symmetric

                    
                self.vols.append(pySpline.volume(
                        ku=ku,kv=kv,kw=kw,coef=blocks[ivol],
                        no_print=self.NO_PRINT,
                        tu=uniform_knots(sizes[ivol,0],ku),
                        tv=uniform_knots(sizes[ivol,1],kv),
                        tw=uniform_knots(sizes[ivol,2],kw)))

                # Generate dummy original data:
                U = numpy.zeros((3,3,3))
                V = numpy.zeros((3,3,3))
                W = numpy.zeros((3,3,3))

                for i in xrange(3):
                    for j in xrange(3):
                        for k in xrange(3):
                            U[i,j,k] = float(i)/2
                            V[i,j,k] = float(j)/2
                            W[i,j,k] = float(k)/2
                        # end for
                    # end for
                # end for

                self.vols[-1].X = self.vols[-1](U,V,W)

                self.vols[-1].orig_data = True
                self.vols[-1].Nu = 3
                self.vols[-1].Nv = 3
                self.vols[-1].Nw = 3
            # end for

            self.nVol = len(self.vols)
            self._calcConnectivity(1e-4,1e-4)
            nCtl = self.topo.nGlobal
            self.coef = numpy.zeros((nCtl,3))
            self._setVolumeCoef()

            for ivol in xrange(self.nVol):
                self.vols[ivol]._setFaceSurfaces()
                self.vols[ivol]._setEdgeCurves()
            # end for
        # end if
            
        else:
            for ivol in xrange(nVol):
                self.vols.append(pySpline.volume(
                        X=blocks[ivol],ku=4,kv=4,kw=4,
                        Nctlu=4,Nctlv=4,Nctlw=4,
                        no_print=self.NO_PRINT,
                        recompute=False))
            # end for

            self.nVol = len(self.vols)
        # end if

        return

    def _readBVol(self,*args,**kwargs):
        '''Read a bvol file and produce the volumes'''
        assert 'file_name' in kwargs,'file_name must be specified for plot3d'
        assert 'file_type' in kwargs,'file_type must be specified as binary or ascii'
        file_name = kwargs['file_name']        
        file_type = kwargs['file_type']
        mpiPrint(' ',self.NO_PRINT)
        if file_type == 'ascii':
            mpiPrint('Loading ascii bvol file: %s ...'%(file_name),self.NO_PRINT)
            binary = False
            f = open(file_name,'r')
        else:
            mpiPrint('Loading binary bvol file: %s ...'%(file_name),self.NO_PRINT)
            binary = True
            f = open(file_name,'rb')
        # end for

        self.nVol = geo_utils.readNValues(f,1,'int',binary)
        mpiPrint(' -> nVol = %d'%(self.nVol),self.NO_PRINT)
        self.vols = []
        for ivol in xrange(self.nVol):
            inits = geo_utils.readNValues(f,6,'int',binary) # This is
                                                  # nctlu,nctlv,nctlw,
                                                  # ku,kv,kw
            tu  = geo_utils.readNValues(f,inits[0]+inits[3],'float',binary)
            tv  = geo_utils.readNValues(f,inits[1]+inits[4],'float',binary)
            tw  = geo_utils.readNValues(f,inits[2]+inits[5],'float',binary)
            coef= geo_utils.readNValues(
                f,inits[0]*inits[1]*inits[2]*3,'float',binary).reshape(
                [inits[0],inits[1],inits[2],3])
            self.vols.append(pySpline.volume(\
                    Nctlu=inits[0],Nctlv=inits[1],Nctlw=inits[2],ku=inits[3],
                    kv=inits[4],kw=inits[5],tu=tu,tv=tv,tw=tw,coef=coef))
        # end for
            
    def _readCGNS(self,*args,**kwargs):
        '''Load a CGNS file and create the spline to go with each patch'''
        assert 'file_name' in kwargs,'file_name must be specified for CGNS'
        file_name = kwargs['file_name']
        import pyspline

        mpiPrint(' ',self.NO_PRINT)
        mpiPrint('Loading CGNS file: %s ...'%(file_name),self.NO_PRINT)
        cg,nzones = pyspline.open_cgns(file_name)
        mpiPrint(' -> nVol = %d'%(nzones),self.NO_PRINT)

        blocks = []
        BCs = []
        for i in xrange(nzones):
            zoneshape = pyspline.read_cgns_zone_shape(cg,i+1)
            X,faceBCs = pyspline.read_cgns_zone(cg,i+1,zoneshape[0],zoneshape[1],zoneshape[2])
            blocks.append(X)
            BCs.append(faceBCs)
        # end for

        pyspline.close_cgns(cg)

        # Now create a list of spline volume objects:
        vols = []
        # Note This doesn't actually fit the volumes...just produces
        # the parameterization and knot vectors

        for ivol in xrange(nzones):
            vols.append(pySpline.volume(X=blocks[ivol],ku=2,kv=2,kw=2,
                                        Nctlu=2,Nctlv=2,Nctlw=2,
                                        no_print=self.NO_PRINT,
                                        recompute=False,faceBCs=BCs[ivol]))
        self.vols = vols
        self.nVol = len(vols)
    
        return

    def fitGlobal(self,greedyReorder=False):
        mpiPrint(' ',self.NO_PRINT)
        mpiPrint('Global Fitting',self.NO_PRINT)
        nCtl = self.topo.nGlobal
        mpiPrint(' -> Copying Topology',self.NO_PRINT)
        orig_topo = copy.deepcopy(self.topo)
        
        mpiPrint(' -> Creating global numbering',self.NO_PRINT)
        sizes = []
        for ivol in xrange(self.nVol):
            sizes.append([self.vols[ivol].Nu,self.vols[ivol].Nv,self.vols[ivol].Nw])
        # end for
        
        # Get the Globaling number of the original data
        orig_topo.calcGlobalNumbering(sizes,greedyReorder=greedyReorder) 
        N = orig_topo.nGlobal
        mpiPrint(' -> Creating global point list',self.NO_PRINT)
        pts = numpy.zeros((N,3))
        for ii in xrange(N):
            pts[ii] = self.vols[orig_topo.g_index[ii][0][0]].X[orig_topo.g_index[ii][0][1],
                                                               orig_topo.g_index[ii][0][2],
                                                               orig_topo.g_index[ii][0][3]]
        # end for

        # Get the maximum k (ku,kv,kw for each vol)
        kmax = 2
        for ivol in xrange(self.nVol):
            if self.vols[ivol].ku > kmax:
                kmax = self.vols[ivol].ku
            if self.vols[ivol].kv > kmax:
                kmax = self.vols[ivol].kv
            if self.vols[ivol].kw > kmax:
                kmax = self.vols[ivol].kw
            # end if
        # end for
        nnz = N*kmax*kmax*kmax

        vals = numpy.zeros(nnz)
        row_ptr = [0]
        col_ind = numpy.zeros(nnz,'intc')
        mpiPrint(' -> Calculating Jacobian',self.NO_PRINT)
        for ii in xrange(N):
            ivol = orig_topo.g_index[ii][0][0]
            i = orig_topo.g_index[ii][0][1]
            j = orig_topo.g_index[ii][0][2]
            k = orig_topo.g_index[ii][0][3]

            u = self.vols[ivol].U[i,j,k]
            v = self.vols[ivol].V[i,j,k]
            w = self.vols[ivol].W[i,j,k]
         
            vals,col_ind = self.vols[ivol]._getBasisPt(
                u,v,w,vals,row_ptr[ii],col_ind,self.topo.l_index[ivol])
         
            kinc = self.vols[ivol].ku*self.vols[ivol].kv*self.vols[ivol].kw

            row_ptr.append(row_ptr[-1] + kinc)
        # end for

        # Now we can crop out any additional values in col_ptr and vals
        vals    = vals[:row_ptr[-1]]
        col_ind = col_ind[:row_ptr[-1]]

        # Now make a sparse matrix

        NN = sparse.csr_matrix((vals,col_ind,row_ptr))
        mpiPrint(' -> Multiplying N^T * N',self.NO_PRINT)
        NNT = NN.T
        NTN = NNT*NN
        mpiPrint(' -> Factorizing...',self.NO_PRINT)
        solve = factorized(NTN)
        mpiPrint(' -> Back Solving...',self.NO_PRINT)
        self.coef = numpy.zeros((nCtl,3))
        for idim in xrange(3):
            self.coef[:,idim] = solve(NNT*pts[:,idim])
        # end for

        mpiPrint(' -> Setting Volume Coefficients...',self.NO_PRINT)
        self._updateVolumeCoef()
        for ivol in xrange(self.nVol):
            self.vols[ivol]._setFaceSurfaces()
            self.vols[ivol]._setEdgeCurves()

        mpiPrint(' -> Fitting Finished...',self.NO_PRINT)

# ----------------------------------------------------------------------
#                     Topology Information Functions
# ----------------------------------------------------------------------    

    def doConnectivity(self,file_name=None,node_tol=1e-4,edge_tol=1e-4,greedyReorder=False):
        '''
        This is the only public edge connectivity function. 
        If file_name exists it loads the file OR it calculates the connectivity
        and saves to that file.
        Required:
            file_name: filename for con file
        Optional:
            node_tol: The tolerance for identical nodes
            edge_tol: The tolerance for midpoints of edge being identical
        Returns:
            None
            '''
        if file_name is not None and os.path.isfile(file_name):
            mpiPrint(' ',self.NO_PRINT)
            mpiPrint('Reading Connectivity File: %s'%(file_name),self.NO_PRINT)
            self.topo = geo_utils.BlockTopology(file=file_name)
            if self.init_type != 'bvol':
                self._propagateKnotVectors()
            # end if
        else:
            mpiPrint(' ',self.NO_PRINT)
            self._calcConnectivity(node_tol,edge_tol)
            self._propagateKnotVectors()
            if file_name is not None:
                mpiPrint('Writing Connectivity File: %s'%(file_name),self.NO_PRINT)
                self.topo.writeConnectivity(file_name)
            # end if
        # end if

        sizes = []
        for ivol in xrange(self.nVol):
            sizes.append([self.vols[ivol].Nctlu,self.vols[ivol].Nctlv,
                              self.vols[ivol].Nctlw])
        self.topo.calcGlobalNumbering(sizes,greedyReorder=greedyReorder)
        return 

    def _calcConnectivity(self,node_tol,edge_tol):
        # Determine the blocking connectivity

        # Compute the corners
        coords = numpy.zeros((self.nVol,26,3))
        for ivol in xrange(self.nVol):
            for icorner in xrange(8):
                coords[ivol,icorner] = self.vols[ivol].getOrigValueCorner(icorner)
            # end for
            for iedge in xrange(12):
                coords[ivol,8+iedge] = self.vols[ivol].getMidPointEdge(iedge)
            # end for
            for iface in xrange(6):
                coords[ivol,20+iface] = self.vols[ivol].getMidPointFace(iface)
            # end for
        # end for

        self.topo = geo_utils.BlockTopology(coords)
        sizes = []
        for ivol in xrange(self.nVol):
            sizes.append([self.vols[ivol].Nctlu,self.vols[ivol].Nctlv,
                          self.vols[ivol].Nctlw])
        self.topo.calcGlobalNumbering(sizes)

        return

#     def getAttachedFaces(self):
#         ''' Return a boolean array of size nVolx6 where the entry is
#         True if the face is matched to another and False otherwise'''

#         # Loop over all faces and add 1 to each unique face
#         faceAttached = numpy.zeros((nVol,6),dtype=bool)
#         for iVol in xrange(self.nVol):
#             for iFace in xrange(6):
#                 face_count[self.topo.face_link[iVol,iFace]] += 1
#             # end for
#         # end for

#         # Now loop back over and if there was more than one face added
#         # to the unique face, set faceAttached to True for thatf face
#         face_count = numpy.zeros(self.topo.nFace)
#         for iVol in xrange(self.nVol):
#             for iFace in xrange(6):
#                 if face_count[self.topo.face_link[iVol,iFace]] > 1:
#                     faceAttached[iVo, iFace] = True
#                 # end if
#             # end for
#         # end for
                
#         return faceAttached

    def printConnectivity(self):
        '''
        Print the connectivity to the screen
        Required:
            None
        Returns:
            None
            '''
        self.topo.printEdgeConnectivity()
        return
  
    def _propagateKnotVectors(self):
        ''' Propage the knot vectors to make consistent'''
     
        nDG = -1
        ncoef = []
        for i in xrange(self.topo.nEdge):
            if self.topo.edges[i].dg > nDG:
                nDG = self.topo.edges[i].dg
                ncoef.append(self.topo.edges[i].N)
            # end if
        # end for
        nDG += 1
        
    	for ivol in xrange(self.nVol):
            dg_u = self.topo.edges[self.topo.edge_link[ivol][0]].dg
            dg_v = self.topo.edges[self.topo.edge_link[ivol][2]].dg
            dg_w = self.topo.edges[self.topo.edge_link[ivol][8]].dg
            self.vols[ivol].Nctlu = ncoef[dg_u]
            self.vols[ivol].Nctlv = ncoef[dg_v]
            self.vols[ivol].Nctlw = ncoef[dg_w]
            if self.vols[ivol].ku < self.vols[ivol].Nctlu:
                if self.vols[ivol].Nctlu > 4:
	            self.vols[ivol].ku = 4
                else:
                    self.vols[ivol].ku = self.vols[ivol].Nctlu
		# endif
            # end if
            if self.vols[ivol].kv < self.vols[ivol].Nctlv:
		if self.vols[ivol].Nctlv > 4:
                    self.vols[ivol].kv = 4
                else:
                    self.vols[ivol].kv = self.vols[ivol].Nctlv
                # end if
            # end if

            if self.vols[ivol].kw < self.vols[ivol].Nctlw:
		if self.vols[ivol].Nctlw > 4:
                    self.vols[ivol].kw = 4
                else:
                    self.vols[ivol].kw = self.vols[ivol].Nctlw
                # end if
            # end if

            self.vols[ivol]._calcKnots()
            # Now loop over the number of design groups, accumulate all
            # the knot vectors that coorspond to this dg, then merge them all
        # end for
        
        for idg in xrange(nDG):
            knot_vectors = []
            flip = []
            for ivol in xrange(self.nVol):
                for iedge in xrange(12):
                    if self.topo.edges[self.topo.edge_link[ivol][iedge]].dg == idg:
                        if self.topo.edge_dir[ivol][iedge] == -1:
                            flip.append(True)
                        else:
                            flip.append(False)
                        # end if
                        if iedge in [0,1,4,5]:
                            knot_vec = self.vols[ivol].tu
                        elif iedge in [2,3,6,7]:
                            knot_vec = self.vols[ivol].tv
                        elif iedge in [8,9,10,11]:
                            knot_vec = self.vols[ivol].tw
                        # end if

                        if flip[-1]:
                            knot_vectors.append((1-knot_vec)[::-1].copy())
                        else:
                            knot_vectors.append(knot_vec)
                        # end if
                    # end if
                # end for
            # end for
           
            # Now blend all the knot vectors
            new_knot_vec = geo_utils.blendKnotVectors(knot_vectors,False)
            new_knot_vec_flip = (1-new_knot_vec)[::-1]
            # And reset them all
            counter = 0
            for ivol in xrange(self.nVol):
                for iedge in xrange(12):
                    if self.topo.edges[self.topo.edge_link[ivol][iedge]].dg == idg:
                        if iedge in [0,1,4,5]:
                            if flip[counter] == True:
                                self.vols[ivol].tu = new_knot_vec_flip.copy()
                            else:
                                self.vols[ivol].tu = new_knot_vec.copy()
                            # end if
                        elif iedge in [2,3,6,7]:
                            if flip[counter] == True:
                                self.vols[ivol].tv = new_knot_vec_flip.copy()
                            else:
                                self.vols[ivol].tv = new_knot_vec.copy()
                            # end if
                        elif iedge in [8,9,10,11]:
                            if flip[counter] == True:
                                self.vols[ivol].tw = new_knot_vec_flip.copy()
                            else:
                                self.vols[ivol].tw = new_knot_vec.copy()
                            # end if
                        # end if
                        counter += 1
                    # end if
                # end for
                self.vols[ivol]._setCoefSize()
            # end for
        # end for (dg loop)

        return    


# ----------------------------------------------------------------------
#                        Output Functions
# ----------------------------------------------------------------------    

    def writeTecplot(self,file_name,vols=True,coef=True,orig=False,
                     vol_labels=False,edge_labels=False,node_labels=False):


        '''Write the pyGeo Object to Tecplot dat file
        Required:
            file_name: The filename for the output file
        Optional:
            vols: boolean, write the interpolated volumes
            coef: boolean, write the control points
            vol_labels: boolean, write the surface labels
            '''

        # Open File and output header
        
        f = pySpline.openTecplot(file_name,3)

        # --------------------------------------
        #    Write out the Interpolated Surfaces
        # --------------------------------------

        if vols == True:
            for ivol in xrange(self.nVol):
                self.vols[ivol]._writeTecplotVolume(f)

        # --------------------------------------
        #    Write out the Original Grid
        # --------------------------------------
        
        if orig == True:
            for ivol in xrange(self.nVol):
                pySpline.writeTecplot3D(f,'orig',self.vols[ivol].X)

        # -------------------------------
        #    Write out the Control Points
        # -------------------------------
        
        if coef == True:
            for ivol in xrange(self.nVol):
                pySpline.writeTecplot3D(f,'coef',self.vols[ivol].coef)

        # ---------------------------------------------
        #    Write out The Volume Labels
        # ---------------------------------------------
        if vol_labels == True:
            # Split the filename off
            (dirName,fileName) = os.path.split(file_name)
            (fileBaseName, fileExtension)=os.path.splitext(fileName)
            label_filename = dirName+'./'+fileBaseName+'.vol_labels.dat'
            f2 = open(label_filename,'w')
            for ivol in xrange(self.nVol):
                midu = floor(self.vols[ivol].Nctlu/2)
                midv = floor(self.vols[ivol].Nctlv/2)
                midw = floor(self.vols[ivol].Nctlw/2)
                text_string = 'TEXT CS=GRID3D, X=%f,Y=%f,Z=%f, T=\"V%d\"\n'%(self.vols[ivol].coef[midu,midv,midw,0],self.vols[ivol].coef[midu,midv,midw,1], self.vols[ivol].coef[midu,midv,midw,2],ivol)
                f2.write('%s'%(text_string))
            # end for 
            f2.close()
        # end if 
        if edge_labels == True:
            # Split the filename off
            (dirName,fileName) = os.path.split(file_name)
            (fileBaseName, fileExtension)=os.path.splitext(fileName)
            label_filename = dirName+'./'+fileBaseName+'.edge_labels.dat'
            f2 = open(label_filename,'w')
            for ivol in xrange(self.nVol):
                for iedge in xrange(12):
                    pt = self.vols[ivol].edge_curves[iedge](0.5)
                    edge_id = self.topo.edge_link[ivol][iedge]
                    text_string = 'TEXT CS=GRID3D X=%f,Y=%f,Z=%f,T=\"E%d\"\n'%(pt[0],pt[1],pt[2],edge_id)
                    f2.write('%s'%(text_string))
                # end for
            # end for 
            f2.close()

        if node_labels == True:
            # First we need to figure out where the corners actually *are*
            n_nodes = len(unique(self.topo.node_link.flatten()))
            node_coord = numpy.zeros((n_nodes,3))

            for i in xrange(n_nodes):
                # Try to find node i
                for ivol in xrange(self.nVol):
                    for inode  in xrange(8):
                        if self.topo.node_link[ivol][inode] == i:
                            coordinate = self.vols[ivol].getValueCorner(inode)
                        # end if
                    # end for
                # end for
                node_coord[i] = coordinate
            # end for
            # Split the filename off
            (dirName,fileName) = os.path.split(file_name)
            (fileBaseName, fileExtension)=os.path.splitext(fileName)
            label_filename = dirName+'./'+fileBaseName+'.node_labels.dat'
            f2 = open(label_filename,'w')
            for i in xrange(n_nodes):
                text_string = 'TEXT CS=GRID3D, X=%f,Y=%f,Z=%f,T=\"n%d\"\n'%(
                    node_coord[i][0],node_coord[i][1],node_coord[i][2],i)
                f2.write('%s'%(text_string))
            # end for 
            f2.close()
        # end if

        pySpline.closeTecplot(f)
        return

    def writeBvol(self,file_name,binary=False):
        '''Write the pyBlock volumes to a file. This is the equilivent
        of the iges file for the surface version. 
        '''
        if binary:
            f = open(file_name,'wb')
            numpy.array(self.nVol).tofile(f,sep="")
        else:
            f = open(file_name,'w')
            f.write('%d\n'%(self.nVol))
        # end for
        for ivol in xrange(self.nVol):
            self.vols[ivol]._writeBvol(f,binary)
        # end for
        
        return

    def writePlot3d(self,file_name,binary=False):
        '''Write the grid to a plot3d file'''

        sizes = []
        for ivol in xrange(self.nVol):
            sizes.append(self.vols[ivol].Nu)
            sizes.append(self.vols[ivol].Nv)
            sizes.append(self.vols[ivol].Nw)
        # end for
        
        if binary:
            f = open(file_name,'wb')
            numpy.array(self.nVol).tofile(f,sep="")
            numpy.array(sizes).tofile(f,sep="")
            for ivol in xrange(self.nVol):
                vals = self.vols[ivol](self.vols[ivol].U,self.vols[ivol].V,
                                       self.vols[ivol].W)
                vals[:,:,:,0].flatten(1).tofile(f,sep="")
                vals[:,:,:,1].flatten(1).tofile(f,sep="")
                vals[:,:,:,2].flatten(1).tofile(f,sep="")
            # end for
        else:
            f = open(file_name,'w')
            f.write('%d\n'%(self.nVol))
            numpy.array(sizes).tofile(f,sep=" ")
            f.write('\n')
            for ivol in xrange(self.nVol):
                vals = self.vols[ivol](self.vols[ivol].U,self.vols[ivol].V,
                                       self.vols[ivol].W)
                vals[:,:,:,0].flatten(1).tofile(f,sep="\n")
                f.write('\n')
                vals[:,:,:,1].flatten(1).tofile(f,sep="\n")
                f.write('\n')
                vals[:,:,:,2].flatten(1).tofile(f,sep="\n")
                f.write('\n')
            # end for
        # end if
        f.close()
        
        return
    
    def getCoefQuality(self):
        '''Get the list of quality for each of the volumes'''
        quality = numpy.array([],'d')
        for ivol in xrange(self.nVol):
            quality = append(quality,self.vols[ivol].getCoefQuality())
        # end for
        return quality

    def getCoefQualityDeriv(self):
        '''Get the derivative of the quality list'''
        # Get the number of volumes
        counter = 0
        for ivol in xrange(self.nVol):
            counter += (self.vols[ivol].Nctlu-1)*(self.vols[ivol].Nctlv-1)*(self.vols[ivol].Nctlw-1)
        # end if
        nQuality = counter
        # The number of non-zeros is EXACTLY 24*number of volumes (8 points per vol*3dof/pt)
        vals = numpy.zeros(nQuality*24)
        col_ind = numpy.zeros(nQuality*24,'intc')
        row_ptr = linspace(0,nQuality*24,nQuality+1).astype('intc')

        counter = 0 
        for ivol in xrange(self.nVol):
            vals,col_ind = self.vols[ivol].getCoefQualityDeriv(counter,self.topo.l_index[ivol],vals,col_ind)
            counter += (self.vols[ivol].Nctlu-1)*(self.vols[ivol].Nctlv-1)*(self.vols[ivol].Nctlw-1)*24
        # end for

        dQdx = sparse.csr_matrix((vals,col_ind,row_ptr),shape=(nQuality,3*len(self.coef)))

        return dQdx

    def verifyCoefQualityDeriv(self):
        for ivol in xrange(self.nVol):
            self.vols[ivol].verifyQualityDeriv()
        # end for
            
        return

# ----------------------------------------------------------------------
#               Update Functions
# ----------------------------------------------------------------------    
    def _updateVolumeCoef(self):
        '''Copy the pyBlock list of control points back to the volumes'''
        for ii in xrange(len(self.coef)):
            for jj in xrange(len(self.topo.g_index[ii])):
                ivol  = self.topo.g_index[ii][jj][0]
                i     = self.topo.g_index[ii][jj][1]
                j     = self.topo.g_index[ii][jj][2]
                k     = self.topo.g_index[ii][jj][3]
                self.vols[ivol].coef[i,j,k] = self.coef[ii].astype('d')
            # end for
        # end for
        return

    def _setVolumeCoef(self):
        '''Set the volumecoef list from the pySpline volumes'''
        self.coef = numpy.zeros((self.topo.nGlobal,3))
        for ivol in xrange(self.nVol):
            vol = self.vols[ivol]
            for i in xrange(vol.Nctlu):
                for j in xrange(vol.Nctlv):
                    for k in xrange(vol.Nctlw):
                        self.coef[self.topo.l_index[ivol][i,j,k]] = \
                            vol.coef[i,j,k]
                # end for
            # end for
        # end for

        return 

    def _calcdPtdCoef(self, index):
        '''Calculate the (fixed) volume derivative of a discrete set of ponits'''
        volID = self.embeded_volumes[index].volID
        u       = self.embeded_volumes[index].u
        v       = self.embeded_volumes[index].v
        w       = self.embeded_volumes[index].w
        N       = self.embeded_volumes[index].N
        mpiPrint('Calculating Volume %s Derivative for %d Points...'%(index,len(volID)),self.NO_PRINT)

        # Get the maximum k (ku or kv for each surface)
        kmax = 2
        for ivol in xrange(self.nVol):
            if self.vols[ivol].ku > kmax:
                kmax = self.vols[ivol].ku
            if self.vols[ivol].kv > kmax:
                kmax = self.vols[ivol].kv
            if self.vols[ivol].kw > kmax:
                kmax = self.vols[ivol].kw
            # end if
        # end for
        nnz = N*kmax*kmax*kmax
        vals = numpy.zeros(nnz)
        row_ptr = [0]
        col_ind = numpy.zeros(nnz,'intc')
        for i in xrange(N):
            kinc = self.vols[volID[i]].ku*self.vols[volID[i]].kv*self.vols[volID[i]].kw
            vals,col_ind = self.vols[volID[i]]._getBasisPt(\
                u[i],v[i],w[i],vals,row_ptr[i],col_ind,self.topo.l_index[volID[i]])

            row_ptr.append(row_ptr[-1] + kinc)
            if self.embeded_volumes[index].mask is not None:
                if not i in self.embeded_volumes[index].mask:
                    # Kill the values we just added
                    vals[row_ptr[-2]:row_ptr[-1]] = 0.0
                # end if
            # end if

        # Now we can crop out any additional values in col_ptr and vals
        vals    = vals[:row_ptr[-1]]
        col_ind = col_ind[:row_ptr[-1]]
        # Now make a sparse matrix iff we actually have coordinates
        if N > 0:
            self.embeded_volumes[index].dPtdCoef = sparse.csr_matrix((vals,col_ind,row_ptr),
                                                                     shape=[N,len(self.coef)])
        mpiPrint('  -> Finished Embeded Volume %s Derivative'%(index),self.NO_PRINT)
        
        return

    def getAttachedPoints(self, index):
        '''
        Return all the volume points for an embedded volume with index index
        Required:
            index: the index for the embeded volume
        Returns:
            coordinates: an aray of the volume points
            '''

        volID = self.embeded_volumes[index].volID
        u     = self.embeded_volumes[index].u
        v     = self.embeded_volumes[index].v
        w     = self.embeded_volumes[index].w
        N     = self.embeded_volumes[index].N
        mask  = self.embeded_volumes[index].mask
        coordinates = numpy.zeros((N,3))
        #print 'Mask',index,mask
        if mask is None:
            for i in xrange(N):
                coordinates[i] = self.vols[volID[i]].getValue(u[i],v[i],w[i])
            # end for
        else:
            for j in xrange(len(mask)):
                i = mask[j]
                coordinates[i] = self.vols[volID[i]].getValue(u[i],v[i],w[i])
                # end if
            # end for
        # end if

        return coordinates

# ----------------------------------------------------------------------
#             Embeded Geometry Functions
# ----------------------------------------------------------------------    

    def attachPoints(self, coordinates, index, interiorOnly=False,
                     faceFreeze=None,*args, **kwargs):
        '''Embed a set of coordinates into all volumes

        coordintes: the coordinates
        index: is a string name to identify the set of points
        
        '''
        eps = 1e-12
        # Generate coef_mask regardless
        coef_mask = []
        for iVol in xrange(self.nVol):
            coef_mask.append(numpy.zeros((self.vols[iVol].Nctlu, 
                                          self.vols[iVol].Nctlv, 
                                          self.vols[iVol].Nctlw), dtype=bool))

        # Project Points
        if not coordinates==None:
            timeA = time.time()
            if not interiorOnly:
                volID,u,v,w,D = self.projectPoints(coordinates, checkErrors=True,
                                                   *args, **kwargs)
                self.embeded_volumes[index] = embeded_volume(volID, u, v, w)
            else:
                volID,u,v,w,D = self.projectPoints(coordinates, checkErrors=False,
                                                   *args, **kwargs)
                if faceFreeze is None: # Do the "auto" freezing algorithm:

                    # Determine a characteric 'length', r_star based on FFD size
                    Xmin, Xmax = self.getBounds()
                    r_star = min(abs(Xmax[0] - Xmin[0]), 
                                 abs(Xmax[1] - Xmin[1]), 
                                 abs(Xmax[2] - Xmin[2]))
                    
                    mask = []
                    for i in xrange(len(D)):
                        Dnrm = numpy.linalg.norm(D[i])

                        if Dnrm < 50*eps: # Sufficiently inside
                            mask.append(i)
                        else:
                            # Determine if the points NOT in the volume are within
                            # r_star. If they are, flag the face and the on
                            # inside of that face in coef_mask as True
                            if Dnrm < r_star:
                                if v[i] > eps and v[i] < 1-eps and w[i] > eps and w[i] < 1-eps:
                                    if u[i] < eps:
                                        coef_mask[volID[i]][0, :, :] = True
                                        coef_mask[volID[i]][1, :, :] = True
                                    elif u[i] > 1-eps:
                                        coef_mask[volID[i]][-1, :, :] = True
                                        coef_mask[volID[i]][-2, :, :] = True
                                    # end if
                                elif u[i] > eps and u[i] < 1-eps and w[i] > eps and w[i] < 1-eps:
                                    if v[i] < eps:
                                        coef_mask[volID[i]][:, 0, :] = True
                                        coef_mask[volID[i]][:, 1, :] = True
                                    elif v[i] > 1-eps:
                                        coef_mask[volID[i]][:, -1, :] = True
                                        coef_mask[volID[i]][:, -2, :] = True
                                    # end if
                                elif u[i] > eps and u[i] < 1-eps and v[i] > eps and v[i] < 1-eps:
                                    if w[i] < eps:
                                        coef_mask[volID[i]][:, :, 0] = True
                                        coef_mask[volID[i]][:, :, 1] = True
                                    elif w[i] > 1-eps:
                                        coef_mask[volID[i]][:, :, -1] = True
                                        coef_mask[volID[i]][:, :, -2] = True
                                    # end if
                                # end if
                            # end if
                        # end if
                    # end for
                else: # Use faceFreeze to freeze sepcified faces:

                    # Only let the user do this for one volume. You
                    # will most likely not get what you expect for
                    # multiple volumes:
                    if self.nVol > 1: 
                        mpiPrint('Error: faceFreeze option can only be used with child FFD\'s with \
one volume')
                        sys.exit(1)
                    # end if

                    if 'iLow' in faceFreeze:
                        coef_mask[0][0, :, :] = True
                        coef_mask[0][1, :, :] = True
                    elif 'iHigh' in faceFreeze:
                        coef_mask[0][-1, :, :] = True
                        coef_mask[0][-2, :, :] = True
                    elif 'jLow' in faceFreeze:
                        coef_mask[0][:, 0, :] = True
                        coef_mask[0][:, 1, :] = True
                    elif 'jHigh' in faceFreeze:
                        coef_mask[0][:, -1, :] = True
                        coef_mask[0][:, -2, :] = True
                    elif 'kLow' in faceFreeze:
                        coef_mask[0][:, :, 0] = True
                        coef_mask[0][:, :, 1] = True
                    elif 'kHigh' in faceFreeze:
                        coef_mask[0][:, :, -1] = True
                        coef_mask[0][:, :, -2] = True
                    # end if
                # end if

                # Now that we have the mask we can create the embedded volume

                self.embeded_volumes[index] = embeded_volume(volID, u, v, w, mask)
            # end if

            timeB = time.time()
            mpiPrint('Embedded %d points in %4.2f second: %4.0f coor/sec'%(
                    len(coordinates), timeB-timeA, len(coordinates)/(timeB-timeA)))
        # end

        # Finally we need to convert coef_mask to the flatten global
        # coef type:

        tmp = numpy.zeros(len(self.coef), dtype=bool)
        for iVol in xrange(self.nVol):
            for i in xrange(coef_mask[iVol].shape[0]):
                for j in xrange(coef_mask[iVol].shape[1]):
                    for k in xrange(coef_mask[iVol].shape[2]):
                        ind = self.topo.l_index[iVol][i,j,k]
                        if coef_mask[iVol][i,j,k] == True:
                            tmp[ind] = True
                        # end if
                    # end for
                # end for
            # end for
        # end for

        return tmp

# ----------------------------------------------------------------------
#             Geometric Functions
# ----------------------------------------------------------------------    

    def projectPoints(self, x0, eps=1e-12, checkErrors=True, *args, **kwargs):
        '''Project a point into any one of the volumes. Returns 
        the volID,u,v,w,D of the point in volID or closest to it.
        
        This is still *technically* a inefficient brute force search,
        but it uses some huristics to give a much more efficient
        algorithm. Basically, we use the volume the last point was
        projected in as a "good guess" as to what volume the current
        point falls in. This works since subsequent points are usually
        close together. This will not help for randomly distrubuted
        points.
        '''

        # Make sure we are dealing with a 2D "Nx3" list of points
        x0 = numpy.atleast_2d(x0)

        volID = numpy.zeros(len(x0),'intc')
        u     = numpy.zeros(len(x0))
        v     = numpy.zeros(len(x0))
        w     = numpy.zeros(len(x0))
        D     = 1e10*numpy.ones((len(x0),3))

        # Starting list is just [0,1,2,...,nVol-1]
        vol_list = numpy.arange(self.nVol)
        u0 = 0.0
        v0 = 0.0
        w0 = 0.0
        
        for i in xrange(len(x0)):
            for j in xrange(self.nVol):
                iVol = vol_list[j]
                u0,v0,w0,D0 = self.vols[iVol].projectPoint(
                    x0[i], eps=eps, Niter=100, **kwargs)

                D0_norm =numpy.linalg.norm(D0)
                # If the new distance is less than the previous best
                # distance, set the volID, u,v,w, since this may be
                # best we can do:
                if D0_norm < numpy.linalg.norm(D[i]):
                    volID[i] = iVol
                    u[i]     = u0
                    v[i]     = v0
                    w[i]     = w0
                    D[i]     = D0
                # end if

                # Now, if D0 is close enough to our tolerance, we can
                # exit the loop since we know we won't do any better
                if (D0_norm < eps*50):
                    break
                # end if
            # end for

            # Shuffle the order of the vol_list such that the last
            # volume used (iVol or vol_list[j]) is at the start of the
            # list and the remainder are shuflled towards the back
            vol_list = numpy.hstack([iVol,vol_list[:j],vol_list[j+1:]])
        # end for
        
        # If desired check the errors and print warnings:
        if checkErrors:
            
            # Loop back through the points and determine which ones are
            # bad (> 50*ep) and print them to the screen:
            counter = 0
            D_max = 0.0
            D_rms = 0.0
            bad_pts = []
            for i in xrange(len(x0)):
                nrm = numpy.linalg.norm(D[i])
                if nrm > D_max:
                    D_max = nrm
                # end if

                D_rms += nrm**2

                if nrm > eps*50:
                    counter += 1
                    bad_pts.append([x0[i],D[i]])
                # end if
            # end for
            if len(x0) > 0:
                D_rms = numpy.sqrt(D_rms / len(x0))
            else:
                D_rms = None
            # end if

            # Check to see if we have bad projections and print a warning:
            if counter > 0:
                print ' -> Warning: %d point(s) not projected to tolerance: \
    %g\n.  Max Error: %12.6g ; RMS Error: %12.6g'%(counter,eps,D_max,D_rms)
                print 'List of Points is: (pt, delta):'
                for i in xrange(len(bad_pts)):
                    print '[%12.5g %12.5g %12.5g] [%12.5g %12.5g %12.5g]'%(
                        bad_pts[i][0][0],
                        bad_pts[i][0][1],
                        bad_pts[i][0][2],
                        bad_pts[i][1][0],
                        bad_pts[i][1][1],
                        bad_pts[i][1][2])
                # end for
            # end if
        # end if

        return volID,u,v,w,D

    def getBounds(self):
        '''Determine the extents of the volumes
        Required: 
            None:
        Returns:
            xmin,xmax: xmin is the lowest x,y,z point and xmax the highest
            '''
        Xmin,Xmax = self.vols[0].getBounds()
        for iVol in xrange(1,self.nVol):
            Xmin0,Xmax0 = self.vols[iVol].getBounds()
            for iDim in xrange(3):
                Xmin[iDim] = min(Xmin[iDim],Xmin0[iDim])
                Xmax[iDim] = max(Xmax[iDim],Xmax0[iDim])
            # end for
        # end for

        return Xmin, Xmax
  
class embeded_volume(object):

    def __init__(self, volID, u, v, w, mask=None):
        '''A Container class for a set of embeded volume points
        Requres:
            voliD list of the volume iD's for the points
            uvw: list of the uvw points
            '''
        self.volID = numpy.array(volID)
        self.u = numpy.array(u)
        self.v = numpy.array(v)
        self.w = numpy.array(w)
        self.N = len(self.u)
        self.dPtdCoef = None
        self.dPtdX    = None
        self.mask     = mask

        return
        
#==============================================================================
# Class Test
#==============================================================================
if __name__ == '__main__':
	
    # Run a Simple Test Case
    print 'Testing pyBlock...'
    print 'No tests implemented yet...'

